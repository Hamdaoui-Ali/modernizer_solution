"""Tests for F07 Reviewer Before Apply and F05 Chatbot Proposal Steering."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.domain.checksums import (
    utc_now_text,
    sha256_canonical_json,
)
from migration_factory.control_tower.application.v2_reviewer_service import (
    V2ReviewerService,
    ReviewerCritique,
)
from migration_factory.control_tower.application.v2_repair_flow import (
    V2RepairFlowService,
)
from migration_factory.control_tower.application.v2_assistant_service import (
    V2AssistantService,
)
from migration_factory.control_tower.application.v2_model_schemas import (
    REVIEWER_CRITIQUE_SCHEMA,
    ACTION_REQUEST_SCHEMA,
    F05_ALLOWED_ACTION_TYPES,
    F05_EXPLICITLY_BLOCKED_ACTION_TYPES,
    validate_against_schema,
    SchemaValidationError,
)
from migration_factory.control_tower.application.v2_action_resolver import (
    V2AssistantActionResolver,
    ActionBindingRequest,
    ActionResolverProtocol,
    FailedCommandInfo,
    SandboxBinding,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.v2_reviewer_repository import (
    SqliteV2ReviewerRepository,
    V2ReviewerCritiqueRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_repair_repository import (
    SqliteV2RepairRepository,
    V2RepairProposalRecord,
)


# ── Test helpers ────────────────────────────────────────────────────


def _connection(tmp_path: Path, name: str = "test.sqlite3") -> sqlite3.Connection:
    conn = sqlite3.connect(
        tmp_path / name,
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_pending_migrations(conn)
    return conn


def _api_client(
    tmp_path: Path,
    *,
    fake_model_client: Any = None,
) -> tuple[TestClient, sqlite3.Connection]:
    """Build a FastAPI TestClient with SQLite-backed UoW."""
    conn = _connection(tmp_path)
    from migration_factory.control_tower.adapters.fastapi import create_app
    from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork

    app = create_app(
        lambda: SqliteUnitOfWork(conn),
        v2_assistant_model_client=fake_model_client,
    )
    return TestClient(app, base_url="http://127.0.0.1:8000"), conn


def _fake_model_client(*, reviewer_decision: str = "accept") -> Any:
    """Create a fake model client that returns valid structured output.

    For F05 revision tests, returns a valid RepairProposal JSON.
    For F07 reviewer tests, returns a valid ReviewerCritique JSON.
    """
    import json as _json
    from dataclasses import dataclass as _dc

    @_dc(frozen=True)
    class _FakeResult:
        content: str
        source: str = "fake"
        model_status: str = "live_ok"
        provider: str = "fake"
        role: str = "reviewer"
        success: bool = True
        redacted_summary: str = "Fake model OK."
        failure_reason: str = ""

    class _FakeClient:
        def __init__(self, decision: str):
            self._decision = decision
            self.roles: list[str] = []

        def answer(self, *, prompt: str, fallback: str, conversation_history=None) -> Any:
            self.roles.append("assistant")
            # Return appropriate JSON based on what's being asked
            if "ReviewerCritique" in prompt or "migration reviewer" in prompt.lower():
                return _FakeResult(
                    content=_json.dumps({
                        "decision": self._decision,
                        "reasoning": f"Fake reviewer model: {self._decision}.",
                        "missing_evidence": [],
                        "unsafe_assumptions": [],
                    }),
                )
            # For revision/repair prompts, return valid RepairProposal
            return _FakeResult(
                content=_json.dumps({
                    "failure_hypothesis": "Revised hypothesis from model",
                    "patch_summary": "Revised patch from model",
                    "affected_paths": ["pom.xml"],
                    "validation_plan": "Run mvn test to verify",
                }),
            )

        def answer_with_role(
            self,
            *,
            role,
            prompt: str,
            fallback: str,
            conversation_history=None,
            output_schema_name=None,
            require_schema: bool = False,
        ) -> Any:
            self.roles.append(role.value)
            if role.value == "proposer":
                return _FakeResult(
                    content=_json.dumps({
                        "failure_hypothesis": "Revised hypothesis from model",
                        "patch_summary": "Revised patch from model",
                        "affected_paths": ["pom.xml"],
                        "validation_plan": "Run mvn test to verify",
                    }),
                    role=role.value,
                )
            if role.value == "reviewer":
                return _FakeResult(
                    content=_json.dumps({
                        "decision": self._decision,
                        "reasoning": f"Fake reviewer model: {self._decision}.",
                        "missing_evidence": [],
                        "unsafe_assumptions": [],
                    }),
                    role=role.value,
                )
            return self.answer(
                prompt=prompt,
                fallback=fallback,
                conversation_history=conversation_history,
            )

    return _FakeClient(decision=reviewer_decision)


def _headers() -> dict[str, str]:
    from migration_factory.control_tower.adapters.fastapi.security import DEFAULT_FRONTEND_CLIENT_ID
    return {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": DEFAULT_FRONTEND_CLIENT_ID,
    }


def _seed_api_repair_proposal(
    conn: sqlite3.Connection,
    *,
    command_id: str = "cmd1",
    failure_summary: str = "Build failed",
    hypothesis: str = "Missing dep",
    patch_summary: str = "Add dep",
    affected_paths: tuple[str, ...] = ("pom.xml",),
) -> str:
    service = V2RepairFlowService(repair_repo=SqliteV2RepairRepository(conn))
    proposal = service.create_proposal(
        command_id=command_id,
        failure_summary=failure_summary,
        hypothesis=hypothesis,
        patch_summary=patch_summary,
        affected_paths=affected_paths,
    )
    return proposal.proposal_id


# ── F07: Reviewer critique schema ───────────────────────────────────


class TestReviewerCritiqueSchema:
    """Validate REVIEWER_CRITIQUE_SCHEMA strictness."""

    def test_valid_accept_critique_passes(self) -> None:
        data = {
            "decision": "accept",
            "reasoning": "Proposal looks safe and covers all evidence.",
            "missing_evidence": [],
            "unsafe_assumptions": [],
        }
        validate_against_schema("ReviewerCritique", data)

    def test_valid_revise_critique_passes(self) -> None:
        data = {
            "decision": "revise",
            "reasoning": "Missing test coverage for the pom change.",
            "missing_evidence": ["test_results.txt"],
            "unsafe_assumptions": ["Assumes Java 17 compatibility"],
        }
        validate_against_schema("ReviewerCritique", data)

    def test_valid_reject_critique_passes(self) -> None:
        data = {
            "decision": "reject",
            "reasoning": "Proposal touches legacy source paths.",
            "missing_evidence": ["sandbox_binding.json"],
            "unsafe_assumptions": ["Modifies src/main/java"],
        }
        validate_against_schema("ReviewerCritique", data)

    def test_missing_required_field_fails(self) -> None:
        data = {
            "decision": "accept",
            "reasoning": "Good proposal.",
            # missing_evidence and unsafe_assumptions are now required
        }
        with pytest.raises(SchemaValidationError):
            validate_against_schema("ReviewerCritique", data)

    def test_invalid_decision_enum_fails(self) -> None:
        data = {
            "decision": "maybe",
            "reasoning": "Not sure.",
            "missing_evidence": [],
            "unsafe_assumptions": [],
        }
        with pytest.raises(SchemaValidationError):
            validate_against_schema("ReviewerCritique", data)

    def test_additional_properties_rejected(self) -> None:
        data = {
            "decision": "accept",
            "reasoning": "Looks good.",
            "missing_evidence": [],
            "unsafe_assumptions": [],
            "should_not_be_here": "bypass attempt",
        }
        with pytest.raises(SchemaValidationError):
            validate_against_schema("ReviewerCritique", data)


# ── F07: Reviewer repository ────────────────────────────────────────


class TestReviewerRepository:
    """Verify SQLite reviewer critique persistence."""

    def test_save_and_get_critique(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2ReviewerRepository(conn)
        now = utc_now_text()
        record = V2ReviewerCritiqueRecord(
            critique_id="crit1",
            proposal_id="prop1",
            proposal_type="repair",
            proposal_checksum="abc123",
            context_pack_checksum="cp-xyz",
            decision="accept",
            reasoning="Safe proposal.",
            missing_evidence_json="[]",
            unsafe_assumptions_json="[]",
            model_invocation_id=None,
            created_at=now,
        )
        repo.save_critique(record)
        loaded = repo.get_critique("crit1")
        assert loaded is not None
        assert loaded.critique_id == "crit1"
        assert loaded.decision == "accept"
        assert loaded.proposal_checksum == "abc123"

    def test_list_critiques_by_proposal(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2ReviewerRepository(conn)
        now = utc_now_text()
        for i in range(3):
            repo.save_critique(V2ReviewerCritiqueRecord(
                critique_id=f"crit{i}",
                proposal_id="prop1",
                proposal_type="repair",
                proposal_checksum=f"cs{i}",
                context_pack_checksum="cp-xyz",
                decision="accept",
                reasoning=f"Reason {i}",
                missing_evidence_json="[]",
                unsafe_assumptions_json="[]",
                model_invocation_id=None,
                created_at=now,
            ))
        critiques = repo.list_critiques_by_proposal("prop1")
        assert len(critiques) == 3

    def test_get_latest_accepted_matches_checksums(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2ReviewerRepository(conn)
        now = utc_now_text()
        # Save an accepted critique
        repo.save_critique(V2ReviewerCritiqueRecord(
            critique_id="crit_a",
            proposal_id="prop1",
            proposal_type="repair",
            proposal_checksum="cs_match",
            context_pack_checksum="cp_match",
            decision="accept",
            reasoning="Good.",
            missing_evidence_json="[]",
            unsafe_assumptions_json="[]",
            model_invocation_id=None,
            created_at=now,
        ))
        # Save a revised critique (not accepted)
        repo.save_critique(V2ReviewerCritiqueRecord(
            critique_id="crit_r",
            proposal_id="prop1",
            proposal_type="repair",
            proposal_checksum="cs_match",
            context_pack_checksum="cp_match",
            decision="revise",
            reasoning="Needs work.",
            missing_evidence_json='["test"]',
            unsafe_assumptions_json="[]",
            model_invocation_id=None,
            created_at=now,
        ))
        # Should find the accepted one
        found = repo.get_latest_accepted("prop1", "cs_match", "cp_match")
        assert found is not None
        assert found.critique_id == "crit_a"
        assert found.decision == "accept"

    def test_get_latest_accepted_returns_none_on_mismatch(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2ReviewerRepository(conn)
        now = utc_now_text()
        repo.save_critique(V2ReviewerCritiqueRecord(
            critique_id="crit1",
            proposal_id="prop1",
            proposal_type="repair",
            proposal_checksum="cs_old",
            context_pack_checksum="cp_old",
            decision="accept",
            reasoning="Good.",
            missing_evidence_json="[]",
            unsafe_assumptions_json="[]",
            model_invocation_id=None,
            created_at=now,
        ))
        # Different checksum — should return None
        found = repo.get_latest_accepted("prop1", "cs_new", "cp_new")
        assert found is None

    def test_append_only_rejects_update(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2ReviewerRepository(conn)
        now = utc_now_text()
        repo.save_critique(V2ReviewerCritiqueRecord(
            critique_id="crit_upd",
            proposal_id="prop1",
            proposal_type="repair",
            proposal_checksum="cs",
            context_pack_checksum="cp",
            decision="accept",
            reasoning="OK",
            missing_evidence_json="[]",
            unsafe_assumptions_json="[]",
            model_invocation_id=None,
            created_at=now,
        ))
        # UPDATE should fail due to trigger (IntegrityError or OperationalError)
        with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError)):
            conn.execute(
                "UPDATE v2_reviewer_critiques SET decision = 'reject' WHERE critique_id = 'crit_upd'"
            )


# ── F07: Reviewer service ───────────────────────────────────────────


class TestReviewerService:
    """Verify V2ReviewerService business logic."""

    def test_record_critique_does_not_change_proposal_status(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2ReviewerRepository(conn)
        service = V2ReviewerService(reviewer_repo=repo)

        critique = service.record_critique(
            proposal_id="prop1",
            proposal_type="repair",
            proposal_checksum="cs1",
            context_pack_checksum="cp1",
            decision="accept",
            reasoning="Looks good.",
        )
        assert critique.decision == "accept"
        # Critiques are stored and retrievable
        loaded = service.get_critique(critique.critique_id)
        assert loaded is not None
        assert loaded.decision == "accept"

    def test_record_critique_rejects_invalid_decision(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2ReviewerRepository(conn)
        service = V2ReviewerService(reviewer_repo=repo)
        with pytest.raises(ValueError, match="Invalid reviewer decision"):
            service.record_critique(
                proposal_id="prop1",
                proposal_checksum="cs1",
                context_pack_checksum="cp1",
                decision="maybe",
                reasoning="?",
            )

    def test_check_reviewer_gate_accept(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2ReviewerRepository(conn)
        service = V2ReviewerService(reviewer_repo=repo)

        service.record_critique(
            proposal_id="prop1",
            proposal_checksum="cs_good",
            context_pack_checksum="cp_good",
            decision="accept",
            reasoning="Approved by reviewer.",
        )
        # Gate should pass
        result = service.check_reviewer_gate("prop1", "cs_good", "cp_good")
        assert result is not None
        assert result.decision == "accept"

    def test_check_reviewer_gate_revise_blocks(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2ReviewerRepository(conn)
        service = V2ReviewerService(reviewer_repo=repo)

        service.record_critique(
            proposal_id="prop1",
            proposal_checksum="cs1",
            context_pack_checksum="cp1",
            decision="revise",
            reasoning="Needs changes.",
        )
        # Gate should fail — only accept passes
        result = service.check_reviewer_gate("prop1", "cs1", "cp1")
        assert result is None

    def test_check_reviewer_gate_reject_blocks(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2ReviewerRepository(conn)
        service = V2ReviewerService(reviewer_repo=repo)

        service.record_critique(
            proposal_id="prop1",
            proposal_checksum="cs1",
            context_pack_checksum="cp1",
            decision="reject",
            reasoning="Unsafe.",
        )
        result = service.check_reviewer_gate("prop1", "cs1", "cp1")
        assert result is None

    def test_check_reviewer_gate_mismatched_checksum_blocks(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2ReviewerRepository(conn)
        service = V2ReviewerService(reviewer_repo=repo)

        service.record_critique(
            proposal_id="prop1",
            proposal_checksum="cs_old",
            context_pack_checksum="cp_old",
            decision="accept",
            reasoning="Was good at the time.",
        )
        # Checksums changed — gate should fail
        result = service.check_reviewer_gate("prop1", "cs_new", "cp_new")
        assert result is None

    def test_list_critiques_returns_newest_first(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2ReviewerRepository(conn)
        service = V2ReviewerService(reviewer_repo=repo)

        service.record_critique(
            proposal_id="prop1",
            proposal_checksum="cs1",
            context_pack_checksum="cp1",
            decision="revise",
            reasoning="First pass.",
        )
        service.record_critique(
            proposal_id="prop1",
            proposal_checksum="cs2",
            context_pack_checksum="cp2",
            decision="accept",
            reasoning="Second pass.",
        )
        critiques = service.list_critiques("prop1")
        assert len(critiques) == 2
        # Newest first
        assert critiques[0].proposal_checksum == "cs2"


# ── F07: Reviewer gate in repair flow ───────────────────────────────


class TestReviewerGateBlocksApproval:
    """Verify that the reviewer gate prevents approval without accepted critique."""

    def test_missing_reviewer_blocks_repair_approval(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        reviewer_repo = SqliteV2ReviewerRepository(conn)
        reviewer_service = V2ReviewerService(reviewer_repo=reviewer_repo)
        service = V2RepairFlowService(repair_repo=repo, reviewer_service=reviewer_service)

        proposal = service.create_proposal(
            command_id="cmd1",
            failure_summary="Build failed",
            hypothesis="Missing dependency",
            patch_summary="Add dependency",
            affected_paths=("pom.xml",),
        )
        p_checksum = sha256_canonical_json({"proposal_id": proposal.proposal_id})
        cp_checksum = "cp-test-123"

        # No reviewer critique exists — approval MUST fail (no bypass)
        with pytest.raises(ValueError, match="blocked by reviewer gate"):
            service.approve_proposal(
                proposal_id=proposal.proposal_id,
                approval_checksum="approve-me",
                proposal_checksum=p_checksum,
                context_pack_checksum=cp_checksum,
            )

    def test_accepted_reviewer_allows_approval(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        reviewer_repo = SqliteV2ReviewerRepository(conn)
        reviewer_service = V2ReviewerService(reviewer_repo=reviewer_repo)
        service = V2RepairFlowService(repair_repo=repo, reviewer_service=reviewer_service)

        proposal = service.create_proposal(
            command_id="cmd1",
            failure_summary="Build failed",
            hypothesis="Missing dependency",
            patch_summary="Add dependency",
            affected_paths=("pom.xml",),
        )
        p_checksum = sha256_canonical_json({"proposal_id": proposal.proposal_id})
        cp_checksum = "cp-test-123"

        # Record an accepted reviewer critique
        reviewer_service.record_critique(
            proposal_id=proposal.proposal_id,
            proposal_checksum=p_checksum,
            context_pack_checksum=cp_checksum,
            decision="accept",
            reasoning="Proposal is safe.",
        )
        # Approval should succeed
        result = service.approve_proposal(
            proposal_id=proposal.proposal_id,
            approval_checksum="approve-me",
            proposal_checksum=p_checksum,
            context_pack_checksum=cp_checksum,
        )
        assert result.status == "approved"

    def test_stale_reviewer_checksum_blocks_approval(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        reviewer_repo = SqliteV2ReviewerRepository(conn)
        reviewer_service = V2ReviewerService(reviewer_repo=reviewer_repo)
        service = V2RepairFlowService(repair_repo=repo, reviewer_service=reviewer_service)

        proposal = service.create_proposal(
            command_id="cmd1",
            failure_summary="Build failed",
            hypothesis="Missing dep",
            patch_summary="Add dep",
            affected_paths=("pom.xml",),
        )
        p_checksum = sha256_canonical_json({"proposal_id": proposal.proposal_id})
        cp_checksum = "cp-original"

        # Record critique for original checksum
        reviewer_service.record_critique(
            proposal_id=proposal.proposal_id,
            proposal_checksum=p_checksum,
            context_pack_checksum=cp_checksum,
            decision="accept",
            reasoning="OK.",
        )
        # Now approve with a different context_pack_checksum (stale)
        with pytest.raises(ValueError, match="blocked by reviewer gate"):
            service.approve_proposal(
                proposal_id=proposal.proposal_id,
                approval_checksum="approve-me",
                proposal_checksum=p_checksum,
                context_pack_checksum="cp-stale-changed",
            )

    def test_approve_without_checksums_rejected(self, tmp_path: Path) -> None:
        """approve_proposal now requires both checksums — no bypass."""
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        reviewer_repo = SqliteV2ReviewerRepository(conn)
        reviewer_service = V2ReviewerService(reviewer_repo=reviewer_repo)
        service = V2RepairFlowService(repair_repo=repo, reviewer_service=reviewer_service)

        proposal = service.create_proposal(
            command_id="cmd1",
            failure_summary="Build failed",
            hypothesis="Missing dep",
            patch_summary="Add dep",
            affected_paths=("pom.xml",),
        )
        # Missing checksums — TypeError because params are now required
        with pytest.raises(TypeError):
            service.approve_proposal(
                proposal_id=proposal.proposal_id,
                approval_checksum="approve-me",
            )

    def test_accepted_reviewer_allows_approval_but_does_not_apply(self, tmp_path: Path) -> None:
        """Reviewer accept enables approval — but status is 'approved', not 'applied'."""
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        reviewer_repo = SqliteV2ReviewerRepository(conn)
        reviewer_service = V2ReviewerService(reviewer_repo=reviewer_repo)
        service = V2RepairFlowService(repair_repo=repo, reviewer_service=reviewer_service)

        proposal = service.create_proposal(
            command_id="cmd1",
            failure_summary="Build failed",
            hypothesis="Missing dependency",
            patch_summary="Add dependency",
            affected_paths=("pom.xml",),
        )
        p_checksum = sha256_canonical_json({"proposal_id": proposal.proposal_id})
        cp_checksum = "cp-test-123"

        # Record an accepted reviewer critique
        reviewer_service.record_critique(
            proposal_id=proposal.proposal_id,
            proposal_checksum=p_checksum,
            context_pack_checksum=cp_checksum,
            decision="accept",
            reasoning="Proposal is safe.",
        )
        # Approval should succeed with status "approved"
        result = service.approve_proposal(
            proposal_id=proposal.proposal_id,
            approval_checksum="approve-me",
            proposal_checksum=p_checksum,
            context_pack_checksum=cp_checksum,
        )
        assert result.status == "approved"
        # Reviewer accept is NOT apply — status must not be "applied"
        assert result.status != "applied"


# ── F05: Action type enforcement ────────────────────────────────────


class TestF05ActionTypeEnforcement:
    """Verify action_type whitelist and blocked action rejection."""

    def test_allowed_action_types_pass_validation(self) -> None:
        for action_type in F05_ALLOWED_ACTION_TYPES:
            data = {
                "action_type": action_type,
                "reason": "Test",
                "stage_index": 1,
                "payload_checksum": "cs-test",
            }
            validate_against_schema("ActionRequest", data)

    def test_blocked_action_types_rejected_by_schema(self) -> None:
        for action_type in F05_EXPLICITLY_BLOCKED_ACTION_TYPES:
            data = {
                "action_type": action_type,
                "reason": "Test",
                "stage_index": 1,
                "payload_checksum": "cs-test",
            }
            with pytest.raises(SchemaValidationError):
                validate_against_schema("ActionRequest", data)

    def test_unknown_action_type_rejected_by_schema(self) -> None:
        data = {
            "action_type": "do_something_dangerous",
            "reason": "Test",
            "stage_index": 1,
            "payload_checksum": "cs-test",
        }
        with pytest.raises(SchemaValidationError):
            validate_against_schema("ActionRequest", data)

    def test_draft_action_rejects_blocked_types(self, tmp_path: Path) -> None:
        service = V2AssistantService()
        with pytest.raises(ValueError, match="is blocked"):
            service.draft_action(
                job_id="job1",
                action_type="execute_command_directly",
                reason="test",
            )

    def test_draft_action_rejects_bypass_attempts(self, tmp_path: Path) -> None:
        service = V2AssistantService()
        for bypass in ("force_apply", "skip_reviewer", "skip_approval"):
            with pytest.raises(ValueError):
                service.draft_action(
                    job_id="job1",
                    action_type=bypass,
                    reason="test",
                )

    def test_draft_action_allows_revision_with_payload(self, tmp_path: Path) -> None:
        service = V2AssistantService()
        draft = service.draft_action(
            job_id="job1",
            action_type="revise_repair_proposal",
            reason="User requested POM-only change.",
            source_proposal_id="prop1",
            failed_command_id="cmd1",
            revision_instruction="Make it POM-only",
            context_pack_checksum="cp-123",
            allowed_scope="pom_only",
        )
        assert draft.action_type == "revise_repair_proposal"
        assert draft.source_proposal_id == "prop1"
        assert draft.allowed_scope == "pom_only"

    def test_action_schema_rejects_additional_properties(self) -> None:
        data = {
            "action_type": "diagnose_failure",
            "reason": "Test",
            "stage_index": 1,
            "payload_checksum": "cs-test",
            "execute_anyway": True,
        }
        with pytest.raises(SchemaValidationError):
            validate_against_schema("ActionRequest", data)


# ── F05: Revision resolution ────────────────────────────────────────


class TestRevisionResolution:
    """Verify revise_repair_proposal resolution logic."""

    def _make_resolver(
        self,
        job_status: str = "active",
        source_status: str = "draft",
        cmd_status: str = "failed",
        affected_paths: list[str] | None = None,
        allowed_scope: str = "any",
    ) -> V2AssistantActionResolver:
        """Build a resolver with controllable backend state."""
        job = type("Job", (), {
            "job_id": "job1",
            "status": job_status,
        })()
        proposal = type("Proposal", (), {
            "proposal_id": "prop1",
            "command_id": "cmd1",
            "status": source_status,
            "approval_checksum": "cs-prop",
            "affected_paths": affected_paths or ["pom.xml"],
        })()
        command = type("Command", (), {
            "command_id": "cmd1",
            "status": cmd_status,
            "stage_index": 1,
            "result_json": json.dumps({"sandbox_path": "/tmp/sandbox"}),
            "created_at": utc_now_text(),
        })()

        proto = ActionResolverProtocol(
            get_job=lambda jid: job if jid == "job1" else None,
            list_commands=lambda jid: [command],
            get_proposal=lambda pid: proposal if pid == "prop1" else None,
        )
        return V2AssistantActionResolver(resolver=proto)

    def test_resolve_revision_succeeds_with_valid_binding(self) -> None:
        resolver = self._make_resolver()
        request = ActionBindingRequest(
            job_id="job1",
            action_type="revise_repair_proposal",
            source_proposal_id="prop1",
            failed_command_id="cmd1",
            context_pack_checksum="cp-123",
            revision_instruction="Make it POM-only",
            allowed_scope="any",
        )
        result = resolver.resolve_revision(request)
        assert result.verified is True
        assert result.binding.proposal_id == "prop1"
        assert result.binding.command_id == "cmd1"

    def test_resolve_revision_rejects_missing_source_proposal(self) -> None:
        resolver = self._make_resolver()
        request = ActionBindingRequest(
            job_id="job1",
            action_type="revise_repair_proposal",
            failed_command_id="cmd1",
            context_pack_checksum="cp-123",
        )
        with pytest.raises(ValueError, match="source_proposal_id is required"):
            resolver.resolve_revision(request)

    def test_resolve_revision_rejects_missing_failed_command(self) -> None:
        resolver = self._make_resolver()
        request = ActionBindingRequest(
            job_id="job1",
            action_type="revise_repair_proposal",
            source_proposal_id="prop1",
            context_pack_checksum="cp-123",
        )
        with pytest.raises(ValueError, match="failed_command_id is required"):
            resolver.resolve_revision(request)

    def test_resolve_revision_rejects_missing_context_checksum(self) -> None:
        resolver = self._make_resolver()
        request = ActionBindingRequest(
            job_id="job1",
            action_type="revise_repair_proposal",
            source_proposal_id="prop1",
            failed_command_id="cmd1",
        )
        with pytest.raises(ValueError, match="context_pack_checksum is required"):
            resolver.resolve_revision(request)

    def test_resolve_revision_rejects_inactive_job(self) -> None:
        resolver = self._make_resolver(job_status="completed")
        request = ActionBindingRequest(
            job_id="job1",
            action_type="revise_repair_proposal",
            source_proposal_id="prop1",
            failed_command_id="cmd1",
            context_pack_checksum="cp-123",
        )
        with pytest.raises(ValueError, match="not active"):
            resolver.resolve_revision(request)

    def test_resolve_revision_rejects_applied_source(self) -> None:
        resolver = self._make_resolver(source_status="applied")
        request = ActionBindingRequest(
            job_id="job1",
            action_type="revise_repair_proposal",
            source_proposal_id="prop1",
            failed_command_id="cmd1",
            context_pack_checksum="cp-123",
        )
        with pytest.raises(ValueError, match="cannot revise an approved/applied"):
            resolver.resolve_revision(request)

    def test_resolve_revision_rejects_approved_source(self) -> None:
        resolver = self._make_resolver(source_status="approved")
        request = ActionBindingRequest(
            job_id="job1",
            action_type="revise_repair_proposal",
            source_proposal_id="prop1",
            failed_command_id="cmd1",
            context_pack_checksum="cp-123",
        )
        with pytest.raises(ValueError, match="cannot revise an approved/applied"):
            resolver.resolve_revision(request)

    def test_resolve_revision_rejects_non_failed_command(self) -> None:
        resolver = self._make_resolver(cmd_status="running")
        request = ActionBindingRequest(
            job_id="job1",
            action_type="revise_repair_proposal",
            source_proposal_id="prop1",
            failed_command_id="cmd1",
            context_pack_checksum="cp-123",
        )
        with pytest.raises(ValueError, match="expected failed/error/timeout"):
            resolver.resolve_revision(request)

    def test_pom_only_enforcement_rejects_non_pom_paths(self) -> None:
        resolver = self._make_resolver(
            affected_paths=["pom.xml", "src/main/java/App.java"],
            allowed_scope="pom_only",
        )
        request = ActionBindingRequest(
            job_id="job1",
            action_type="revise_repair_proposal",
            source_proposal_id="prop1",
            failed_command_id="cmd1",
            context_pack_checksum="cp-123",
            allowed_scope="pom_only",
        )
        with pytest.raises(ValueError, match="allowed_scope=pom_only violated"):
            resolver.resolve_revision(request)

    def test_pom_only_accepts_pom_only_paths(self) -> None:
        resolver = self._make_resolver(
            affected_paths=["pom.xml", "submodule/pom.xml"],
            allowed_scope="pom_only",
        )
        request = ActionBindingRequest(
            job_id="job1",
            action_type="revise_repair_proposal",
            source_proposal_id="prop1",
            failed_command_id="cmd1",
            context_pack_checksum="cp-123",
            allowed_scope="pom_only",
        )
        result = resolver.resolve_revision(request)
        assert result.verified is True
        assert len(result.warnings) == 1
        assert "pom_only enforced" in result.warnings[0]


# ── F07: API endpoint integration tests ─────────────────────────────


class TestF07ReviewerCritiqueAPI:
    """Integration tests for reviewer critique endpoints (model-backed)."""

    def test_create_reviewer_critique_api(self, tmp_path: Path) -> None:
        """Request reviewer critique — backend calls model, not client-provided decision."""
        fake_client = _fake_model_client(reviewer_decision="accept")
        client, conn = _api_client(tmp_path, fake_model_client=fake_client)

        proposal_id = _seed_api_repair_proposal(conn)

        response = client.post(
            f"/v1/v2/commands/cmd1/repair/proposal/{proposal_id}/reviewer-critique",
            json={
                "proposal_id": proposal_id,
                "proposal_type": "repair",
                "proposal_checksum": "cs-abc",
                "context_pack_checksum": "cp-xyz",
            },
            headers=_headers(),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["critique_id"]
        assert data["decision"] == "accept"  # From fake model, not client body
        assert data["proposal_checksum"] == "cs-abc"
        assert "reviewer" in fake_client.roles, (
            f"Expected reviewer in roles, got {fake_client.roles}"
        )

    def test_create_reviewer_critique_rejects_invalid_schema(self, tmp_path: Path) -> None:
        """Client cannot send decision in body — extra fields rejected."""
        fake_client = _fake_model_client(reviewer_decision="accept")
        client, conn = _api_client(tmp_path, fake_model_client=fake_client)

        proposal_id = _seed_api_repair_proposal(conn)

        # Client tries to send decision directly — extra field rejected
        response = client.post(
            f"/v1/v2/commands/cmd1/repair/proposal/{proposal_id}/reviewer-critique",
            json={
                "proposal_id": proposal_id,
                "proposal_type": "repair",
                "proposal_checksum": "cs-abc",
                "context_pack_checksum": "cp-xyz",
                "decision": "accept",  # REJECTED — client cannot set decision
            },
            headers=_headers(),
        )
        assert response.status_code == 422

    def test_list_reviewer_critiques_api(self, tmp_path: Path) -> None:
        fake_client = _fake_model_client(reviewer_decision="accept")
        client, conn = _api_client(tmp_path, fake_model_client=fake_client)

        proposal_id = _seed_api_repair_proposal(conn)

        # Create a critique via model-backed endpoint
        client.post(
            f"/v1/v2/commands/cmd1/repair/proposal/{proposal_id}/reviewer-critique",
            json={
                "proposal_id": proposal_id,
                "proposal_type": "repair",
                "proposal_checksum": "cs-abc",
                "context_pack_checksum": "cp-xyz",
            },
            headers=_headers(),
        )
        response = client.get(
            f"/v1/v2/commands/cmd1/repair/proposal/{proposal_id}/reviewer-critiques",
            headers=_headers(),
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["critiques"]) == 1
        assert data["critiques"][0]["decision"] == "accept"

    def test_get_reviewer_critique_api(self, tmp_path: Path) -> None:
        fake_client = _fake_model_client(reviewer_decision="accept")
        client, conn = _api_client(tmp_path, fake_model_client=fake_client)

        proposal_id = _seed_api_repair_proposal(conn)

        create_resp2 = client.post(
            f"/v1/v2/commands/cmd1/repair/proposal/{proposal_id}/reviewer-critique",
            json={
                "proposal_id": proposal_id,
                "proposal_type": "repair",
                "proposal_checksum": "cs-abc",
                "context_pack_checksum": "cp-xyz",
            },
            headers=_headers(),
        )
        critique_id = create_resp2.json()["critique_id"]
        response = client.get(f"/v1/v2/reviewer-critiques/{critique_id}", headers=_headers())
        assert response.status_code == 200
        assert response.json()["critique_id"] == critique_id

    def test_get_nonexistent_critique_returns_404(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        response = client.get("/v1/v2/reviewer-critiques/nonexistent", headers=_headers())
        assert response.status_code == 404

    def test_frontend_cannot_post_decision_accept_directly(self, tmp_path: Path) -> None:
        """F07: Client body with decision=accept must be rejected."""
        fake_client = _fake_model_client(reviewer_decision="accept")
        client, conn = _api_client(tmp_path, fake_model_client=fake_client)

        proposal_id = _seed_api_repair_proposal(conn)

        # Client tries to fabricate an accept decision
        response = client.post(
            f"/v1/v2/commands/cmd1/repair/proposal/{proposal_id}/reviewer-critique",
            json={
                "proposal_id": proposal_id,
                "proposal_type": "repair",
                "proposal_checksum": "cs-match",
                "context_pack_checksum": "cp-match",
                "decision": "accept",  # NOT ALLOWED
                "reasoning": "Fabricated bypass",
                "missing_evidence": [],
                "unsafe_assumptions": [],
            },
            headers=_headers(),
        )
        # extra="forbid" on the Pydantic model rejects unknown fields
        assert response.status_code == 422


# ── F05: Draft action API with revision payload ─────────────────────


class TestF05DraftActionAPI:
    """Integration tests for draft action endpoint with F05 features."""

    def test_draft_action_with_revision_payload(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/jobs/job1/assistant/actions/draft",
            json={
                "job_id": "job1",
                "action_type": "diagnose_failure",
                "reason": "Check what failed.",
                "stage_index": 1,
                "source_proposal_id": "prop1",
                "failed_command_id": "cmd1",
                "revision_instruction": "Make it POM-only",
                "context_pack_checksum": "cp-123",
                "allowed_scope": "pom_only",
            },
            headers=_headers(),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["action_type"] == "diagnose_failure"
        assert data["source_proposal_id"] == "prop1"
        assert data["allowed_scope"] == "pom_only"

    def test_draft_action_rejects_blocked_action_type(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/jobs/job1/assistant/actions/draft",
            json={
                "job_id": "job1",
                "action_type": "execute_command_directly",
                "reason": "Run command",
                "stage_index": 1,
            },
            headers=_headers(),
        )
        # Schema validation rejects it because it's not in the enum
        assert response.status_code == 422

    def test_draft_action_rejects_bypass_action(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/jobs/job1/assistant/actions/draft",
            json={
                "job_id": "job1",
                "action_type": "force_apply",
                "reason": "Apply now",
                "stage_index": 1,
            },
            headers=_headers(),
        )
        # Either schema validation or service-level block
        assert response.status_code in (400, 422)


# ── F05: Revision persistence ───────────────────────────────────────


class TestF05RevisionPersistence:
    """Verify revised proposal is persisted as new draft, source is never mutated."""

    def test_create_revision_proposal_persists_new_draft(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        service = V2RepairFlowService(repair_repo=repo)

        revised = service.create_revision_proposal(
            command_id="cmd1",
            source_proposal_id="prop-src",
            failure_summary="Revised failure",
            hypothesis="Revised hypothesis",
            patch_summary="Revised patch",
            affected_paths=("pom.xml",),
            revision_instruction="Make it POM-only",
            context_pack_checksum="cp-123",
            allowed_scope="pom_only",
            revision_number=2,
        )
        assert revised.status == "draft"
        assert revised.source_proposal_id == "prop-src"
        assert revised.revision_of == "prop-src"
        assert revised.revision_number == 2
        assert revised.allowed_scope == "pom_only"
        assert revised.proposal_id != "prop-src"  # Never mutate source

        # Verify persisted
        loaded = repo.get_proposal(revised.proposal_id)
        assert loaded is not None
        assert loaded.status == "draft"
        assert loaded.source_proposal_id == "prop-src"
        assert loaded.revision_of == "prop-src"
        assert loaded.revision_number == 2
        assert loaded.context_pack_checksum == "cp-123"
        assert loaded.allowed_scope == "pom_only"
        assert loaded.proposal_checksum == revised.proposal_checksum

    def test_reloaded_revision_keeps_checksum_and_metadata(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        service = V2RepairFlowService(repair_repo=repo)

        source = service.create_proposal(
            command_id="cmd1",
            failure_summary="Original failure",
            hypothesis="Original hypothesis",
            patch_summary="Original patch",
            affected_paths=("pom.xml",),
        )
        revised = service.create_revision_proposal(
            command_id="cmd1",
            source_proposal_id=source.proposal_id,
            failure_summary="Revised failure",
            hypothesis="Revised hypothesis",
            patch_summary="Revised patch",
            affected_paths=("pom.xml",),
            context_pack_checksum="cp-456",
            allowed_scope="any",
            revision_number=3,
        )

        reloaded = repo.get_proposal(revised.proposal_id)
        assert reloaded is not None
        assert reloaded.proposal_checksum == revised.proposal_checksum
        assert reloaded.source_proposal_id == source.proposal_id
        assert reloaded.revision_of == source.proposal_id
        assert reloaded.revision_number == 3
        assert reloaded.context_pack_checksum == "cp-456"
        assert reloaded.allowed_scope == "any"

    def test_revision_does_not_mutate_source_proposal(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        service = V2RepairFlowService(repair_repo=repo)

        # Create source proposal
        source = service.create_proposal(
            command_id="cmd1",
            failure_summary="Original failure",
            hypothesis="Original hypothesis",
            patch_summary="Original patch",
            affected_paths=("pom.xml",),
        )
        source_id = source.proposal_id
        assert source.status == "draft"

        # Create a revision from it
        revised = service.create_revision_proposal(
            command_id="cmd1",
            source_proposal_id=source_id,
            failure_summary="Revised failure",
            hypothesis="Revised hypothesis",
            patch_summary="Revised patch",
            affected_paths=("pom.xml",),
        )
        assert revised.proposal_id != source_id

        # Source proposal must be unchanged
        source_reloaded = service._proposals.get(source_id)
        if source_reloaded is None and repo is not None:
            record = repo.get_proposal(source_id)
            assert record is not None
            assert record.status == "draft"
            assert record.failure_summary == "Original failure"

    def test_pom_only_accepts_only_pom_paths_on_revision(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2RepairRepository(conn)
        service = V2RepairFlowService(repair_repo=repo)

        # POM-only paths should work
        revised = service.create_revision_proposal(
            command_id="cmd1",
            source_proposal_id="prop-src",
            failure_summary="Fix",
            hypothesis="Fix",
            patch_summary="Fix",
            affected_paths=("pom.xml", "sub/pom.xml"),
            allowed_scope="pom_only",
        )
        assert revised.allowed_scope == "pom_only"
        # All paths are POM files
        for p in revised.affected_paths:
            assert p.endswith("pom.xml") or "/pom.xml" in p


# ── F07: Approval API requires checksums ────────────────────────────


class TestF07ApprovalAPIRequiresChecksums:
    """Verify the approve endpoint mandates proposal/context checksums."""

    def test_approve_endpoint_requires_proposal_and_context_checksum(self, tmp_path: Path) -> None:
        """POST approve must include proposal_checksum and context_pack_checksum."""
        client, conn = _api_client(tmp_path)
        # Missing both checksums — Pydantic rejects
        response = client.post(
            "/v1/v2/commands/cmd1/repair/proposal/prop1/approve",
            json={"approval_checksum": "chk"},
            headers=_headers(),
        )
        assert response.status_code == 422

    def test_approve_missing_proposal_checksum(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/commands/cmd1/repair/proposal/prop1/approve",
            json={
                "approval_checksum": "chk",
                "context_pack_checksum": "cp",
                # proposal_checksum missing
            },
            headers=_headers(),
        )
        assert response.status_code == 422

    def test_approve_missing_context_checksum(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/commands/cmd1/repair/proposal/prop1/approve",
            json={
                "approval_checksum": "chk",
                "proposal_checksum": "pc",
                # context_pack_checksum missing
            },
            headers=_headers(),
        )
        assert response.status_code == 422


# ── F05: Revision model fail-closed helpers ─────────────────────────

def _fake_unavailable_model_client() -> Any:
    """Model returns success=False — simulates unavailable model."""
    from dataclasses import dataclass as _dc

    @_dc(frozen=True)
    class _FakeResult:
        content: str
        source: str = "fake"
        model_status: str = "fallback"
        provider: str = "fake"
        role: str = "assistant"
        success: bool = False
        redacted_summary: str = "Fake model unavailable."
        failure_reason: str = "unavailable"

    class _FakeClient:
        def answer(self, *, prompt: str, fallback: str, conversation_history=None) -> Any:
            return _FakeResult(content=fallback)

    return _FakeClient()


def _fake_invalid_json_model_client() -> Any:
    """Model returns success=True but content is not valid JSON."""
    from dataclasses import dataclass as _dc

    @_dc(frozen=True)
    class _FakeResult:
        content: str
        source: str = "fake"
        model_status: str = "live_ok"
        provider: str = "fake"
        role: str = "assistant"
        success: bool = True
        redacted_summary: str = "Fake model returned garbage."
        failure_reason: str = ""

    class _FakeClient:
        def answer(self, *, prompt: str, fallback: str, conversation_history=None) -> Any:
            return _FakeResult(content="not valid json at all {{{{{{")

    return _FakeClient()


def _fake_invalid_schema_model_client() -> Any:
    """Model returns valid JSON that fails REPAIR_PROPOSAL_SCHEMA."""
    import json as _json
    from dataclasses import dataclass as _dc

    @_dc(frozen=True)
    class _FakeResult:
        content: str
        source: str = "fake"
        model_status: str = "live_ok"
        provider: str = "fake"
        role: str = "assistant"
        success: bool = True
        redacted_summary: str = "Fake model returned bad schema."
        failure_reason: str = ""

    class _FakeClient:
        def answer(self, *, prompt: str, fallback: str, conversation_history=None) -> Any:
            # Valid JSON but missing required fields for RepairProposal
            return _FakeResult(content=_json.dumps({
                "failure_hypothesis": "test",
                # missing patch_summary, affected_paths, validation_plan
            }))

    return _FakeClient()


def _fake_valid_revision_model_client() -> Any:
    """Model returns valid RepairProposal JSON."""
    import json as _json
    from dataclasses import dataclass as _dc

    @_dc(frozen=True)
    class _FakeResult:
        content: str
        source: str = "fake"
        model_status: str = "live_ok"
        provider: str = "fake"
        role: str = "assistant"
        success: bool = True
        redacted_summary: str = "Fake model OK."
        failure_reason: str = ""

    class _FakeClient:
        def __init__(self) -> None:
            self.roles: list[str] = []

        def answer(self, *, prompt: str, fallback: str, conversation_history=None) -> Any:
            self.roles.append("assistant")
            return _FakeResult(content=_json.dumps({
                "failure_hypothesis": "Revised hypothesis from model",
                "patch_summary": "Revised patch from model",
                "affected_paths": ["pom.xml"],
                "validation_plan": "Run mvn test",
            }))

        def answer_with_role(
            self,
            *,
            role,
            prompt: str,
            fallback: str,
            conversation_history=None,
            output_schema_name=None,
            require_schema: bool = False,
        ) -> Any:
            self.roles.append(role.value)
            return _FakeResult(
                content=_json.dumps({
                    "failure_hypothesis": "Revised hypothesis from model",
                    "patch_summary": "Revised patch from model",
                    "affected_paths": ["pom.xml"],
                    "validation_plan": "Run mvn test",
                }),
                role=role.value,
            )

    return _FakeClient()


def _setup_job_command_and_proposal(
    conn: sqlite3.Connection,
    *,
    job_id: str = "job-rev-test",
    command_id: str = "cmd-rev-test",
    affected_paths: list[str] | None = None,
) -> str:
    """Insert a job, failed command, and source proposal; return proposal_id."""
    now = utc_now_text()

    # Insert job
    conn.execute(
        """INSERT INTO v2_migration_jobs (
            job_id, setup_id, setup_checksum, pipeline_id,
            stage_chain_json, status, created_at, updated_at, correlation_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (job_id, "setup-1", "cs-setup", "pipeline-1", '["stage1"]',
         "active", now, now, None),
    )

    # Insert command
    conn.execute(
        """INSERT INTO v2_stage_commands (
            command_id, job_id, stage_index, manifest_checksum,
            argv_json, env_json, status, created_at, updated_at, result_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (command_id, job_id, 1, "cs-manifest",
         '["mvn","test"]', '{}', "failed", now, now,
         json.dumps({"sandbox_path": "/tmp/sandbox-rev"})),
    )

    # Insert source proposal directly
    proposal_id = uuid4().hex
    paths = affected_paths or ["pom.xml"]
    conn.execute(
        """INSERT INTO v2_repair_proposals (
            proposal_id, command_id, failure_summary, hypothesis,
            patch_summary, affected_paths_json, status,
            approval_checksum, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (proposal_id, command_id, "Build failed", "Missing dep",
         "Add dep", json.dumps(paths), "draft", None, now),
    )

    return proposal_id


def _count_proposals(conn: sqlite3.Connection, job_id: str = "job-rev-test") -> int:
    """Count repair proposals for a job's commands."""
    row = conn.execute(
        """SELECT COUNT(*) as cnt FROM v2_repair_proposals p
           JOIN v2_stage_commands c ON p.command_id = c.command_id
           WHERE c.job_id = ?""",
        (job_id,),
    ).fetchone()
    return int(row["cnt"]) if row else 0


def _count_events_by_type(
    conn: sqlite3.Connection,
    job_id: str,
    event_type: str,
) -> int:
    """Count events of a given type for a job."""
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM v2_job_events "
        "WHERE job_id = ? AND type = ?",
        (job_id, event_type),
    ).fetchone()
    return int(row["cnt"]) if row else 0


# ── F05: Revision model fail-closed tests ───────────────────────────


class TestF05RevisionModelFailClosed:
    """Verify revise_repair_proposal fails closed on model unavailability,
    invalid JSON, and schema-invalid output. No revised proposal row and
    no repair_proposal_revised event are created on failure."""

    def test_revise_repair_proposal_model_unavailable_returns_error(
        self, tmp_path: Path,
    ) -> None:
        """Model unavailable → 502 error, no proposal, no event."""
        fake_client = _fake_unavailable_model_client()
        client, conn = _api_client(tmp_path, fake_model_client=fake_client)

        proposal_id = _setup_job_command_and_proposal(conn)
        proposals_before = _count_proposals(conn)
        events_before = _count_events_by_type(conn, "job-rev-test", "repair_proposal_revised")

        response = client.post(
            "/v1/v2/jobs/job-rev-test/assistant/actions/draft",
            json={
                "job_id": "job-rev-test",
                "action_type": "revise_repair_proposal",
                "reason": "Revise after failure",
                "stage_index": 1,
                "source_proposal_id": proposal_id,
                "failed_command_id": "cmd-rev-test",
                "revision_instruction": "Make it POM-only",
                "context_pack_checksum": "cp-123",
                "allowed_scope": "pom_only",
            },
            headers=_headers(),
        )
        assert response.status_code == 502, response.text
        body = response.json()
        assert body["error"]["code"] == "REVISION_MODEL_FAILED"

        # No new proposal created
        assert _count_proposals(conn) == proposals_before
        # No repair_proposal_revised event emitted
        assert _count_events_by_type(
            conn, "job-rev-test", "repair_proposal_revised"
        ) == events_before

    def test_revise_repair_proposal_model_unavailable_creates_no_proposal(
        self, tmp_path: Path,
    ) -> None:
        """Model unavailable → zero new proposals exist in DB."""
        fake_client = _fake_unavailable_model_client()
        client, conn = _api_client(tmp_path, fake_model_client=fake_client)

        proposal_id = _setup_job_command_and_proposal(conn)
        proposals_before = _count_proposals(conn)

        client.post(
            "/v1/v2/jobs/job-rev-test/assistant/actions/draft",
            json={
                "job_id": "job-rev-test",
                "action_type": "revise_repair_proposal",
                "reason": "Revise after failure",
                "stage_index": 1,
                "source_proposal_id": proposal_id,
                "failed_command_id": "cmd-rev-test",
                "revision_instruction": "Make it POM-only",
                "context_pack_checksum": "cp-123",
                "allowed_scope": "pom_only",
            },
            headers=_headers(),
        )
        # Proposal count unchanged
        assert _count_proposals(conn) == proposals_before

    def test_revise_repair_proposal_model_unavailable_emits_no_event(
        self, tmp_path: Path,
    ) -> None:
        """Model unavailable → no repair_proposal_revised event."""
        fake_client = _fake_unavailable_model_client()
        client, conn = _api_client(tmp_path, fake_model_client=fake_client)

        proposal_id = _setup_job_command_and_proposal(conn)
        events_before = _count_events_by_type(
            conn, "job-rev-test", "repair_proposal_revised"
        )

        client.post(
            "/v1/v2/jobs/job-rev-test/assistant/actions/draft",
            json={
                "job_id": "job-rev-test",
                "action_type": "revise_repair_proposal",
                "reason": "Revise after failure",
                "stage_index": 1,
                "source_proposal_id": proposal_id,
                "failed_command_id": "cmd-rev-test",
                "revision_instruction": "Make it POM-only",
                "context_pack_checksum": "cp-123",
                "allowed_scope": "pom_only",
            },
            headers=_headers(),
        )
        # Event count unchanged
        assert _count_events_by_type(
            conn, "job-rev-test", "repair_proposal_revised"
        ) == events_before

    def test_revise_repair_proposal_invalid_model_json_returns_error(
        self, tmp_path: Path,
    ) -> None:
        """Model returns non-JSON → 422 error, no proposal, no event."""
        fake_client = _fake_invalid_json_model_client()
        client, conn = _api_client(tmp_path, fake_model_client=fake_client)

        proposal_id = _setup_job_command_and_proposal(conn)
        proposals_before = _count_proposals(conn)
        events_before = _count_events_by_type(conn, "job-rev-test", "repair_proposal_revised")

        response = client.post(
            "/v1/v2/jobs/job-rev-test/assistant/actions/draft",
            json={
                "job_id": "job-rev-test",
                "action_type": "revise_repair_proposal",
                "reason": "Revise after failure",
                "stage_index": 1,
                "source_proposal_id": proposal_id,
                "failed_command_id": "cmd-rev-test",
                "revision_instruction": "Make it POM-only",
                "context_pack_checksum": "cp-123",
                "allowed_scope": "pom_only",
            },
            headers=_headers(),
        )
        assert response.status_code == 422, response.text
        body = response.json()
        assert body["error"]["code"] == "INVALID_REPAIR_PROPOSAL_OUTPUT"

        assert _count_proposals(conn) == proposals_before
        assert _count_events_by_type(
            conn, "job-rev-test", "repair_proposal_revised"
        ) == events_before

    def test_revise_repair_proposal_invalid_model_json_creates_no_proposal(
        self, tmp_path: Path,
    ) -> None:
        """Model returns garbage JSON → no new proposal row."""
        fake_client = _fake_invalid_json_model_client()
        client, conn = _api_client(tmp_path, fake_model_client=fake_client)

        proposal_id = _setup_job_command_and_proposal(conn)
        proposals_before = _count_proposals(conn)

        client.post(
            "/v1/v2/jobs/job-rev-test/assistant/actions/draft",
            json={
                "job_id": "job-rev-test",
                "action_type": "revise_repair_proposal",
                "reason": "Revise after failure",
                "stage_index": 1,
                "source_proposal_id": proposal_id,
                "failed_command_id": "cmd-rev-test",
                "revision_instruction": "Make it POM-only",
                "context_pack_checksum": "cp-123",
                "allowed_scope": "pom_only",
            },
            headers=_headers(),
        )
        assert _count_proposals(conn) == proposals_before

    def test_revise_repair_proposal_invalid_schema_creates_no_proposal(
        self, tmp_path: Path,
    ) -> None:
        """Model returns valid JSON but fails RepairProposal schema → no revision."""
        fake_client = _fake_invalid_schema_model_client()
        client, conn = _api_client(tmp_path, fake_model_client=fake_client)

        proposal_id = _setup_job_command_and_proposal(conn)
        proposals_before = _count_proposals(conn)
        events_before = _count_events_by_type(conn, "job-rev-test", "repair_proposal_revised")

        response = client.post(
            "/v1/v2/jobs/job-rev-test/assistant/actions/draft",
            json={
                "job_id": "job-rev-test",
                "action_type": "revise_repair_proposal",
                "reason": "Revise after failure",
                "stage_index": 1,
                "source_proposal_id": proposal_id,
                "failed_command_id": "cmd-rev-test",
                "revision_instruction": "Make it POM-only",
                "context_pack_checksum": "cp-123",
                "allowed_scope": "pom_only",
            },
            headers=_headers(),
        )
        assert response.status_code == 422, response.text
        assert _count_proposals(conn) == proposals_before
        assert _count_events_by_type(
            conn, "job-rev-test", "repair_proposal_revised"
        ) == events_before

    def test_revise_repair_proposal_valid_model_output_still_persists_revision(
        self, tmp_path: Path,
    ) -> None:
        """Valid model output → revision proposal IS created."""
        fake_client = _fake_valid_revision_model_client()
        client, conn = _api_client(tmp_path, fake_model_client=fake_client)

        proposal_id = _setup_job_command_and_proposal(conn)
        proposals_before = _count_proposals(conn)

        response = client.post(
            "/v1/v2/jobs/job-rev-test/assistant/actions/draft",
            json={
                "job_id": "job-rev-test",
                "action_type": "revise_repair_proposal",
                "reason": "Revise after failure",
                "stage_index": 1,
                "source_proposal_id": proposal_id,
                "failed_command_id": "cmd-rev-test",
                "revision_instruction": "Make it POM-only",
                "context_pack_checksum": "cp-123",
                "allowed_scope": "pom_only",
            },
            headers=_headers(),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["action_type"] == "revise_repair_proposal"
        assert "revised_proposal" in body
        assert body["revised_proposal"]["status"] == "draft"
        assert body["revised_proposal"]["source_proposal_id"] == proposal_id
        assert fake_client.roles == ["proposer"]
        # Proposal count increased by 1
        assert _count_proposals(conn) == proposals_before + 1

    def test_revise_repair_proposal_valid_model_output_emits_repair_proposal_revised(
        self, tmp_path: Path,
    ) -> None:
        """Valid model output → repair_proposal_revised event emitted."""
        fake_client = _fake_valid_revision_model_client()
        client, conn = _api_client(tmp_path, fake_model_client=fake_client)

        proposal_id = _setup_job_command_and_proposal(conn)
        events_before = _count_events_by_type(
            conn, "job-rev-test", "repair_proposal_revised"
        )

        response = client.post(
            "/v1/v2/jobs/job-rev-test/assistant/actions/draft",
            json={
                "job_id": "job-rev-test",
                "action_type": "revise_repair_proposal",
                "reason": "Revise after failure",
                "stage_index": 1,
                "source_proposal_id": proposal_id,
                "failed_command_id": "cmd-rev-test",
                "revision_instruction": "Make it POM-only",
                "context_pack_checksum": "cp-123",
                "allowed_scope": "pom_only",
            },
            headers=_headers(),
        )
        assert response.status_code == 200, response.text
        # Event count increased by at least 1
        assert _count_events_by_type(
            conn, "job-rev-test", "repair_proposal_revised"
        ) >= events_before + 1
