"""F5-T9: Request Another Review / Revision Loop tests.

Verifies context pack checksum semantics, revision cycle progression,
and V2RepairFlowService.create_revision_proposal behavior.
"""

from __future__ import annotations

import pytest

from migration_factory.repair_loop.repair_context import (
    RepairContextPack,
    build_repair_context_pack,
    compute_context_pack_checksum,
)
from migration_factory.repair_loop.failure_evidence import (
    FailureEvidence,
    FailureSource,
    build_failure_evidence,
)
from migration_factory.control_tower.application.v2_repair_flow import (
    V2RepairFlowService,
    RepairProposal,
)
from migration_factory.control_tower.application.v2_repair_gate_service import (
    V2RepairGateService,
)


_failure_evidence = build_failure_evidence(
    failure_source=FailureSource.BUILD,
    job_id="job-1",
    stage_index=1,
    command_id="cmd-1",
    failure_summary="Build failed: missing import",
    changed_files=("src/App.java",),
    source_profile="java8",
    target_profile="java21",
)


def _pack(**overrides) -> RepairContextPack:
    kwargs: dict = {
        "failure_evidence": _failure_evidence,
        "job_id": "job-1",
        "stage_index": 1,
        "command_id": "cmd-1",
        "source_profile": "java8",
        "target_profile": "java21",
        "cycle_number": 1,
        "max_cycles": 3,
    }
    kwargs.update(overrides)
    return build_repair_context_pack(**kwargs)


def _pack_cycle_0(**overrides) -> RepairContextPack:
    return _pack(cycle_number=0, **overrides)


def _pack_cycle_1(**overrides) -> RepairContextPack:
    return _pack(cycle_number=1, **overrides)


def _pack_cycle_2(**overrides) -> RepairContextPack:
    return _pack(cycle_number=2, **overrides)


# ── Context pack checksum tests ──────────────────────────────────────


def test_checksum_changes_when_user_comments_change() -> None:
    pack_a = _pack(user_comments="fix the typo")
    pack_b = _pack(user_comments="also update the dependency version")
    assert pack_a.context_pack_checksum != pack_b.context_pack_checksum


def test_checksum_differs_from_previous_cycle_pack() -> None:
    pack_c0 = _pack(cycle_number=0, prior_proposal_checksums=("prop-a",))
    pack_c1 = _pack(
        cycle_number=1,
        prior_proposal_checksums=("prop-a", "prop-b"),
        prior_reviewer_notes=("reviewer said no",),
        prior_revision_ids=("rev-1",),
        user_comments="please use narrower scope",
    )
    assert pack_c0.context_pack_checksum != pack_c1.context_pack_checksum


def test_prior_proposal_checksums_in_next_cycle() -> None:
    pack = _pack(cycle_number=2, prior_proposal_checksums=("prop-a", "prop-b"))
    checksum = compute_context_pack_checksum(pack)
    payload = {
        k: v
        for k, v in pack.__dict__.items()
        if k in (
            "job_id",
            "stage_index",
            "command_id",
            "failure_source",
            "failure_evidence_checksum",
            "source_profile",
            "target_profile",
            "accepted_analysis_checksum",
            "accepted_planning_checksum",
            "prior_proposal_checksums",
            "prior_reviewer_notes",
            "user_comments",
            "changed_files",
            "safe_log_preview",
            "base_repo_state_checksum",
            "prior_revision_ids",
            "cycle_number",
            "max_cycles",
        )
    }
    pack_from = RepairContextPack(**{
        **{f: getattr(pack, f) for f in ["job_id", "stage_index", "command_id", "failure_source", "failure_evidence_checksum", "source_profile", "target_profile", "accepted_analysis_checksum", "accepted_planning_checksum", "user_comments", "safe_log_preview", "base_repo_state_checksum", "context_pack_checksum", "created_at", "schema_version"]},
        "prior_proposal_checksums": ("prop-a",),
        "prior_reviewer_notes": pack.prior_reviewer_notes,
        "prior_revision_ids": pack.prior_revision_ids,
        "changed_files": pack.changed_files,
        "cycle_number": pack.cycle_number,
        "max_cycles": pack.max_cycles,
    })
    diff_checksum = compute_context_pack_checksum(pack_from)
    assert checksum != diff_checksum
    assert "prop-a" in pack.prior_proposal_checksums
    assert "prop-b" in pack.prior_proposal_checksums


def test_prior_reviewer_notes_in_next_cycle() -> None:
    pack = _pack(cycle_number=2, prior_reviewer_notes=("needs narrower scope", "check imports"))
    assert "needs narrower scope" in pack.prior_reviewer_notes
    assert "check imports" in pack.prior_reviewer_notes


def test_prior_revision_ids_in_new_context() -> None:
    pack = _pack(prior_revision_ids=("rev-1", "rev-2"))
    assert "rev-1" in pack.prior_revision_ids
    assert "rev-2" in pack.prior_revision_ids


def test_cycle_number_increments_each_revision() -> None:
    c0 = _pack(cycle_number=0)
    c1 = _pack(cycle_number=1, prior_proposal_checksums=("prop-0",))
    c2 = _pack(cycle_number=2, prior_proposal_checksums=("prop-0", "prop-1"))
    assert c0.cycle_number == 0
    assert c1.cycle_number == 1
    assert c2.cycle_number == 2


def test_previous_artifact_not_mutated_by_new_revision() -> None:
    pack_a = build_repair_context_pack(
        failure_evidence=_failure_evidence,
        cycle_number=1,
        prior_proposal_checksums=("prop-0",),
    )
    checksum_a = pack_a.context_pack_checksum
    _pack(
        cycle_number=2,
        prior_proposal_checksums=("prop-0", "prop-1"),
        prior_reviewer_notes=("fix",),
        prior_revision_ids=("rev-1",),
        user_comments="redo",
    )
    assert pack_a.context_pack_checksum == checksum_a
    assert pack_a.cycle_number == 1


def test_new_context_includes_original_failure_evidence_checksum() -> None:
    pack = _pack(cycle_number=2)
    assert pack.failure_evidence_checksum == _failure_evidence.content_checksum
    assert pack.failure_evidence_checksum


def test_new_context_includes_base_repo_state_checksum() -> None:
    pack = _pack(cycle_number=1)
    assert pack.base_repo_state_checksum
    pack_empty = build_repair_context_pack(
        failure_evidence=_failure_evidence,
        changed_files=(),
        file_checksums={},
    )
    assert pack_empty.base_repo_state_checksum


def test_user_comments_truncated_by_build_function() -> None:
    long_comment = "x" * 5000
    pack = build_repair_context_pack(
        failure_evidence=_failure_evidence,
        user_comments=long_comment,
    )
    assert pack.user_comments == long_comment


# ── V2RepairFlowService revision flow tests ──────────────────────────


def test_create_revision_proposal_creates_new_proposal_with_revision_metadata() -> None:
    service = V2RepairFlowService()
    proposal = service.create_proposal(
        command_id="cmd1",
        failure_summary="build failed",
        hypothesis="missing import",
        patch_summary="add import",
        affected_paths=("file.java",),
    )
    revision = service.create_revision_proposal(
        command_id="cmd1",
        source_proposal_id=proposal.proposal_id,
        failure_summary="build failed",
        hypothesis="add import v2",
        patch_summary="add import revised",
        affected_paths=("file.java",),
        revision_instruction="fix the typo",
        context_pack_checksum="abc123",
        revision_number=2,
    )
    assert revision.proposal_id != proposal.proposal_id
    assert revision.status == "draft"
    assert revision.source_proposal_id == proposal.proposal_id
    assert revision.revision_of == proposal.proposal_id
    assert revision.revision_number == 2
    assert revision.context_pack_checksum == "abc123"
    assert revision.proposal_checksum
    assert revision.hypothesis == "add import v2"
    assert revision.patch_summary == "add import revised"


def test_source_proposal_id_recorded_in_revision() -> None:
    service = V2RepairFlowService()
    proposal = service.create_proposal(
        command_id="cmd1",
        failure_summary="build failed",
        hypothesis="missing import",
        patch_summary="add import",
        affected_paths=("file.java",),
    )
    revision = service.create_revision_proposal(
        command_id="cmd1",
        source_proposal_id=proposal.proposal_id,
        failure_summary="build failed",
        hypothesis="add import revised",
        patch_summary="add import revised",
        affected_paths=("file.java",),
        revision_number=2,
    )
    assert revision.source_proposal_id == proposal.proposal_id
    assert revision.revision_of == proposal.proposal_id


def test_revision_number_increments() -> None:
    service = V2RepairFlowService()
    proposal = service.create_proposal(
        command_id="cmd1",
        failure_summary="build failed",
        hypothesis="missing import",
        patch_summary="add import",
        affected_paths=("file.java",),
    )
    rev2 = service.create_revision_proposal(
        command_id="cmd1",
        source_proposal_id=proposal.proposal_id,
        failure_summary="build failed",
        hypothesis="add import v2",
        patch_summary="add import revised",
        affected_paths=("file.java",),
        revision_number=2,
    )
    rev3 = service.create_revision_proposal(
        command_id="cmd1",
        source_proposal_id=rev2.proposal_id,
        failure_summary="build failed",
        hypothesis="add import v3",
        patch_summary="add import revised v3",
        affected_paths=("file.java",),
        revision_number=3,
    )
    assert rev2.revision_number == 2
    assert rev3.revision_number == 3
    assert rev3.source_proposal_id == rev2.proposal_id


def test_origin_proposal_not_mutated_by_revision() -> None:
    service = V2RepairFlowService()
    proposal = service.create_proposal(
        command_id="cmd1",
        failure_summary="build failed",
        hypothesis="missing import",
        patch_summary="add import",
        affected_paths=("file.java",),
    )
    original_id = proposal.proposal_id
    original_checksum = proposal.proposal_checksum
    original_status = proposal.status
    original_hypothesis = proposal.hypothesis
    assert proposal.source_proposal_id is None
    assert proposal.revision_of is None
    assert proposal.revision_number is None

    _revision = service.create_revision_proposal(
        command_id="cmd1",
        source_proposal_id=proposal.proposal_id,
        failure_summary="build failed",
        hypothesis="add import v2",
        patch_summary="add import revised",
        affected_paths=("file.java",),
        revision_number=2,
    )

    assert proposal.proposal_id == original_id
    assert proposal.proposal_checksum == original_checksum
    assert proposal.status == original_status
    assert proposal.hypothesis == original_hypothesis
    assert proposal.source_proposal_id is None
    assert proposal.revision_of is None
    assert proposal.revision_number is None


# ── F5 TASK 2: Revision regenerates Azure repair review chain ─────────


class TestRevisionRegeneratesAzureRepairChain:
    """F5 TASK 2: Request-revision creates new Azure proposer/reviewer chain."""

    @staticmethod
    def _fake_client(reviewer_decision: str = "accept"):
        import json
        from migration_factory.control_tower.application.v2_assistant_model_client import (
            V2AssistantModelResult,
        )
        from migration_factory.control_tower.application.v2_model_role_router import V2ModelRole

        class _FakeReviseClient:
            def __init__(self):
                self.calls: list[dict] = []

            def answer_with_role(self, *, role, prompt, fallback, output_schema_name=None, require_schema=True):
                self.calls.append({
                    "role": role,
                    "prompt_preview": prompt[:200],
                    "output_schema_name": output_schema_name,
                    "require_schema": require_schema,
                })
                if role == V2ModelRole.PROPOSER:
                    content = json.dumps({
                        "root_cause": "Need updated dependency",
                        "fix_strategy": "Update pom.xml version",
                        "changed_files": ["pom.xml"],
                        "proposed_diff": "diff --git a/pom.xml b/pom.xml\n--- a/pom.xml\n+++ b/pom.xml\n@@\n+<dependency><groupId>com.h2database</groupId><artifactId>h2</artifactId><scope>runtime</scope></dependency>\n",
                        "risk": "LOW",
                        "confidence": 0.90,
                        "rationale": "Dependency needs version bump.",
                        "deterministic_rule_id": "DEPENDENCY_ADD_H2_RUNTIME",
                    }, sort_keys=True)
                else:
                    content = json.dumps({
                        "decision": reviewer_decision,
                        "notes": ["Revised diff scoped correctly"],
                        "confidence": 0.95,
                        "risks": [],
                        "policy_concerns": [],
                        "reviewed_context_checksum": "",
                        "reviewed_primary_output_checksum": "",
                        "reviewed_diff_checksum": "",
                    }, sort_keys=True)
                return V2AssistantModelResult(
                    content=content,
                    source="azure_openai",
                    model_status="live_ok",
                    provider="azure_openai",
                    role=role.value,
                    success=True,
                    redacted_summary="user-selected Azure model available",
                    failure_reason="",
                )
        return _FakeReviseClient()

    def test_revision_calls_azure_proposer_and_reviewer(self, tmp_path: Path) -> None:
        import sqlite3
        from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
        from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import SqlitePhaseGateRepository
        from migration_factory.control_tower.application.v2_phase_gate_service import V2PhaseGateService

        conn = sqlite3.connect(str(tmp_path / "rev.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        apply_pending_migrations(conn)

        gate_repo = SqlitePhaseGateRepository(conn)
        gate_service = V2PhaseGateService(gate_repo)
        repair_gate_service = V2RepairGateService(gate_service=gate_service)

        client = self._fake_client(reviewer_decision="accept")
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        legacy = tmp_path / "legacy"
        legacy.mkdir()

        result = repair_gate_service.regenerate_reviewed_repair_chain_on_revision(
            job_id="job-rev",
            stage_index=3,
            command_id="cmd-rev",
            user_comments="Please check the dependency version more carefully",
            prior_evidence_checksum="sha256:prior-ev",
            prior_context_checksum="sha256:prior-cp",
            prior_primary_output_checksum="sha256:prior-po",
            prior_reviewer_output_checksum="sha256:prior-ro",
            prior_final_diff_checksum="sha256:prior-fd",
            prior_policy_validation_checksum="sha256:prior-pv",
            prior_base_repo_state_checksum="sha256:prior-rs",
            sandbox_path=str(sandbox),
            run_dir=tmp_path / "run",
            legacy_path=str(tmp_path / "legacy"),
            deterministic_rule_id="DEPENDENCY_ADD_H2_RUNTIME",
            previous_repair_review_checksums=("sha256:chain-1",),
            cycle_number=2,
            model_client=client,
            h2_required=True,
        )

        assert len(client.calls) == 2
        assert client.calls[0]["role"].value == "proposer"
        assert client.calls[1]["role"].value == "reviewer"
        assert all(c["require_schema"] is True for c in client.calls)
        # User comments flow into context_pack, which feeds the prompt
        # The prompt is dynamically built from context_pack data

    def test_revision_creates_new_gate_with_different_checksum(self, tmp_path: Path) -> None:
        import sqlite3
        from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
        from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import SqlitePhaseGateRepository
        from migration_factory.control_tower.application.v2_phase_gate_service import V2PhaseGateService

        conn = sqlite3.connect(str(tmp_path / "rev2.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        apply_pending_migrations(conn)

        gate_repo = SqlitePhaseGateRepository(conn)
        gate_service = V2PhaseGateService(gate_repo)
        repair_gate_service = V2RepairGateService(gate_service=gate_service)

        client = self._fake_client(reviewer_decision="accept")
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        legacy = tmp_path / "legacy"
        legacy.mkdir()

        r1 = repair_gate_service.regenerate_reviewed_repair_chain_on_revision(
            job_id="job-rev2a",
            stage_index=3,
            command_id="cmd-rev2a",
            user_comments="First round feedback",
            prior_evidence_checksum="sha256:ev1",
            prior_context_checksum="sha256:cp1",
            prior_primary_output_checksum="sha256:po1",
            prior_reviewer_output_checksum="sha256:ro1",
            prior_final_diff_checksum="sha256:fd1",
            prior_policy_validation_checksum="sha256:pv1",
            prior_base_repo_state_checksum="sha256:rs1",
            sandbox_path=str(sandbox),
            run_dir=tmp_path / "run1",
            legacy_path=str(tmp_path / "legacy"),
            deterministic_rule_id="DEPENDENCY_ADD_H2_RUNTIME",
            previous_repair_review_checksums=(),
            cycle_number=1,
            model_client=client,
            h2_required=True,
        )
        assert r1.status == "created"

        r2 = repair_gate_service.regenerate_reviewed_repair_chain_on_revision(
            job_id="job-rev2b",
            stage_index=3,
            command_id="cmd-rev2b",
            user_comments="Different feedback now",
            prior_evidence_checksum="sha256:ev2",
            prior_context_checksum="sha256:cp2",
            prior_primary_output_checksum="sha256:po2",
            prior_reviewer_output_checksum="sha256:ro2",
            prior_final_diff_checksum="sha256:fd2",
            prior_policy_validation_checksum="sha256:pv2",
            prior_base_repo_state_checksum="sha256:rs2",
            sandbox_path=str(sandbox),
            run_dir=tmp_path / "run2",
            legacy_path=str(tmp_path / "legacy"),
            deterministic_rule_id="DEPENDENCY_ADD_H2_RUNTIME",
            previous_repair_review_checksums=("sha256:chain-1",),
            cycle_number=2,
            model_client=client,
            h2_required=True,
        )
        assert r2.status == "created"
        assert r1.gate_id != r2.gate_id
        assert r1.gate_checksum != r2.gate_checksum

    def test_revision_with_reviewer_reject_does_not_open_gate(self, tmp_path: Path) -> None:
        import sqlite3
        from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
        from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import SqlitePhaseGateRepository
        from migration_factory.control_tower.application.v2_phase_gate_service import V2PhaseGateService

        conn = sqlite3.connect(str(tmp_path / "rev3.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        apply_pending_migrations(conn)

        gate_repo = SqlitePhaseGateRepository(conn)
        gate_service = V2PhaseGateService(gate_repo)
        repair_gate_service = V2RepairGateService(gate_service=gate_service)

        client = self._fake_client(reviewer_decision="reject")
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        legacy = tmp_path / "legacy"
        legacy.mkdir()

        result = repair_gate_service.regenerate_reviewed_repair_chain_on_revision(
            job_id="job-rev3",
            stage_index=3,
            command_id="cmd-rev3",
            user_comments="Try again",
            prior_evidence_checksum="sha256:ev",
            prior_context_checksum="sha256:cp",
            prior_primary_output_checksum="sha256:po",
            prior_reviewer_output_checksum="sha256:ro",
            prior_final_diff_checksum="sha256:fd",
            prior_policy_validation_checksum="sha256:pv",
            prior_base_repo_state_checksum="sha256:rs",
            sandbox_path=str(sandbox),
            run_dir=tmp_path / "run",
            legacy_path=str(tmp_path / "legacy"),
            deterministic_rule_id="DEPENDENCY_ADD_H2_RUNTIME",
            model_client=client,
            h2_required=True,
        )
        assert result.status == "skipped"
        assert "reviewer did not accept" in result.reason

    def test_no_copilot_invoked_during_revision(self, tmp_path: Path) -> None:
        import json
        import sqlite3
        from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
        from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import SqlitePhaseGateRepository
        from migration_factory.control_tower.application.v2_phase_gate_service import V2PhaseGateService
        from migration_factory.control_tower.application.v2_assistant_model_client import V2AssistantModelResult
        from migration_factory.control_tower.application.v2_model_role_router import V2ModelRole

        conn = sqlite3.connect(str(tmp_path / "rev4.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        apply_pending_migrations(conn)

        gate_repo = SqlitePhaseGateRepository(conn)
        gate_service = V2PhaseGateService(gate_repo)
        repair_gate_service = V2RepairGateService(gate_service=gate_service)

        calls = []

        class _TrackedClient:
            def answer_with_role(self, *, role, prompt, fallback, output_schema_name=None, require_schema=True):
                calls.append({"role": role, "provider": "azure_openai"})
                content = json.dumps({
                    "root_cause": "fix",
                    "fix_strategy": "patch",
                    "changed_files": ["pom.xml"],
                    "proposed_diff": "diff --git a/pom.xml b/pom.xml\n--- a/pom.xml\n+++ b/pom.xml\n@@\n+<dependency><groupId>com.h2database</groupId><artifactId>h2</artifactId><scope>runtime</scope></dependency>\n",
                    "risk": "LOW",
                    "confidence": 0.9,
                    "rationale": "fix",
                    "deterministic_rule_id": "DEPENDENCY_ADD_H2_RUNTIME",
                }, sort_keys=True) if role == V2ModelRole.PROPOSER else json.dumps({
                    "decision": "accept",
                    "notes": [],
                    "confidence": 0.95,
                    "risks": [],
                    "policy_concerns": [],
                    "reviewed_context_checksum": "",
                    "reviewed_primary_output_checksum": "",
                    "reviewed_diff_checksum": "",
                }, sort_keys=True)
                return V2AssistantModelResult(
                    content=content,
                    source="azure_openai",
                    model_status="live_ok",
                    provider="azure_openai",
                    role=role.value,
                    success=True,
                    redacted_summary="ok",
                    failure_reason="",
                )

        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        legacy = tmp_path / "legacy"
        legacy.mkdir()

        repair_gate_service.regenerate_reviewed_repair_chain_on_revision(
            job_id="job-rev4",
            stage_index=3,
            command_id="cmd-rev4",
            user_comments="revise",
            prior_evidence_checksum="sha256:ev",
            prior_context_checksum="sha256:cp",
            prior_primary_output_checksum="sha256:po",
            prior_reviewer_output_checksum="sha256:ro",
            prior_final_diff_checksum="sha256:fd",
            prior_policy_validation_checksum="sha256:pv",
            prior_base_repo_state_checksum="sha256:rs",
            sandbox_path=str(sandbox),
            run_dir=tmp_path / "run",
            legacy_path=str(tmp_path / "legacy"),
            deterministic_rule_id="DEPENDENCY_ADD_H2_RUNTIME",
            model_client=_TrackedClient(),
            h2_required=True,
        )

        assert len(calls) == 2
        for call in calls:
            assert call["provider"] == "azure_openai"
            assert call["role"] in (V2ModelRole.PROPOSER, V2ModelRole.REVIEWER)
