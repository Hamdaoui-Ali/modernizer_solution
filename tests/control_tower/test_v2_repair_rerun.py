"""Tests for F5-T11 (Rerun Proof) and F5-T12 (Repeated Failure) — failure evidence repair context."""

from __future__ import annotations

from pathlib import Path

import pytest

from migration_factory.repair_loop.failure_evidence import (
    FailureSource,
    FailureEvidence,
    NormalizedCompilerError,
    NormalizedTestFailure,
    build_failure_evidence,
    compute_failure_content_checksum,
    compute_failure_artifact_checksum,
)
from migration_factory.repair_loop.repair_context import (
    RepairContextPack,
    build_repair_context_pack,
    compute_base_repo_state_checksum,
    compute_context_pack_checksum,
    is_context_pack_stale,
)


# ── F5-T11-1: FailureSource enum values are correct ────────────────────

def test_failure_source_enum_values() -> None:
    assert FailureSource.BUILD.value == "build"
    assert FailureSource.TEST.value == "test"
    assert FailureSource.VALIDATION.value == "validation"
    assert FailureSource.TRANSFORM.value == "transform"
    assert FailureSource.UNKNOWN.value == "unknown"


# ── F5-T11-2: build_failure_evidence with BUILD source ─────────────────

def test_build_failure_evidence_with_build_source() -> None:
    evidence = build_failure_evidence(
        failure_source=FailureSource.BUILD,
        stage_index=1,
        job_id="job-1",
        command_id="cmd-1",
        failure_summary="Compilation error",
        compiler_errors=(
            NormalizedCompilerError(
                message="Cannot find symbol",
                file_path="src/App.java",
                line=42,
                column=10,
                severity="error",
            ),
        ),
    )

    assert evidence.failure_source == FailureSource.BUILD
    assert evidence.stage_index == 1
    assert evidence.job_id == "job-1"
    assert evidence.command_id == "cmd-1"
    assert evidence.failure_summary == "Compilation error"
    assert len(evidence.compiler_errors) == 1
    assert evidence.compiler_errors[0].message == "Cannot find symbol"
    assert evidence.content_checksum != ""
    assert evidence.artifact_checksum != ""


# ── F5-T11-3: build_failure_evidence with TEST source ──────────────────

def test_build_failure_evidence_with_test_source() -> None:
    evidence = build_failure_evidence(
        failure_source=FailureSource.TEST,
        stage_index=2,
        job_id="job-2",
        command_id="cmd-2",
        failure_summary="3 tests failed",
        test_failures=(
            NormalizedTestFailure(
                test_name="testFoo",
                test_class="FooTest",
                message="expected true but was false",
                file_path="src/test/FooTest.java",
            ),
        ),
    )

    assert evidence.failure_source == FailureSource.TEST
    assert evidence.stage_index == 2
    assert len(evidence.test_failures) == 1
    assert evidence.test_failures[0].test_name == "testFoo"
    assert evidence.content_checksum != ""


# ── F5-T11-4: Failure evidence content_checksum changes when source changes ──

def test_failure_evidence_content_checksum_changes_on_source_change() -> None:
    e1 = build_failure_evidence(
        failure_source=FailureSource.BUILD,
        command_id="cmd-1",
        failure_summary="error",
    )
    e2 = build_failure_evidence(
        failure_source=FailureSource.TEST,
        command_id="cmd-1",
        failure_summary="error",
    )

    assert e1.content_checksum != e2.content_checksum


# ── F5-T12-5: Repair context pack includes cycle_number ─────────────────

def test_repair_context_pack_includes_cycle_number() -> None:
    evidence = build_failure_evidence(
        failure_source=FailureSource.BUILD,
        command_id="cmd-1",
        failure_summary="build error",
    )
    pack = build_repair_context_pack(
        failure_evidence=evidence,
        cycle_number=2,
        max_cycles=5,
    )

    assert pack.cycle_number == 2
    assert pack.max_cycles == 5
    assert pack.failure_evidence_checksum == evidence.content_checksum


# ── F5-T12-6: Context pack checksum changes with cycle_number ──────────

def test_context_pack_checksum_changes_with_cycle_number() -> None:
    evidence = build_failure_evidence(
        failure_source=FailureSource.BUILD,
        command_id="cmd-1",
        failure_summary="error",
    )
    pack1 = build_repair_context_pack(
        failure_evidence=evidence,
        cycle_number=0,
    )
    pack2 = build_repair_context_pack(
        failure_evidence=evidence,
        cycle_number=1,
    )

    assert pack1.context_pack_checksum != pack2.context_pack_checksum


# ── F5-T12-7: Attempt tracking: verify max_cycles is propagated ────────

def test_attempt_tracking_max_cycles_propagated() -> None:
    evidence = build_failure_evidence(
        failure_source=FailureSource.BUILD,
        command_id="cmd-1",
        failure_summary="error",
    )
    pack = build_repair_context_pack(
        failure_evidence=evidence,
        max_cycles=3,
        cycle_number=0,
    )

    assert pack.max_cycles == 3
    assert pack.cycle_number == 0


# ── F5-T12-8: Stale context detection when file checksums change ───────

def test_stale_context_detection_when_file_checksums_change() -> None:
    evidence = build_failure_evidence(
        failure_source=FailureSource.BUILD,
        command_id="cmd-1",
        failure_summary="error",
    )
    pack = build_repair_context_pack(
        failure_evidence=evidence,
        file_checksums={"pom.xml": "abc123"},
    )

    stale = is_context_pack_stale(
        pack,
        current_file_checksums={"pom.xml": "def456"},
    )
    assert stale is True

    not_stale = is_context_pack_stale(
        pack,
        current_file_checksums={"pom.xml": "abc123"},
    )
    assert not_stale is False


# ── F5-T12-9: Context pack stability with same inputs ──────────────────

def test_context_pack_stability_with_same_inputs() -> None:
    evidence = build_failure_evidence(
        failure_source=FailureSource.BUILD,
        command_id="cmd-1",
        failure_summary="error",
    )
    pack1 = build_repair_context_pack(
        failure_evidence=evidence,
        job_id="job-1",
        stage_index=2,
        cycle_number=1,
        max_cycles=3,
    )
    pack2 = build_repair_context_pack(
        failure_evidence=evidence,
        job_id="job-1",
        stage_index=2,
        cycle_number=1,
        max_cycles=3,
    )

    assert pack1.context_pack_checksum == pack2.context_pack_checksum


# ── F5-T12-10: Repeated context: verify prior_proposal_checksums grows ─

def test_repeated_context_prior_proposal_checksums_grows() -> None:
    evidence = build_failure_evidence(
        failure_source=FailureSource.BUILD,
        command_id="cmd-1",
        failure_summary="error",
    )
    pack1 = build_repair_context_pack(
        failure_evidence=evidence,
        prior_proposal_checksums=("prop-a-checksum",),
        cycle_number=1,
    )
    pack2 = build_repair_context_pack(
        failure_evidence=evidence,
        prior_proposal_checksums=("prop-a-checksum", "prop-b-checksum"),
        cycle_number=2,
    )

    assert len(pack1.prior_proposal_checksums) == 1
    assert len(pack2.prior_proposal_checksums) == 2
    assert pack1.context_pack_checksum != pack2.context_pack_checksum
    assert pack2.cycle_number == 2


# ── F5 TASK 3: Bounded next repair cycle after rerun failure ──────────


class TestBoundedNextRepairCycle:
    """F5 TASK 3: Rerun failure creates bounded repair cycle or terminal."""

    def test_rerun_failure_creates_second_repair_gate(self, tmp_path: Path) -> None:
        import json, sqlite3
        from migration_factory.control_tower.application.v2_repair_gate_service import V2RepairGateService
        from migration_factory.control_tower.application.v2_phase_gate_service import V2PhaseGateService
        from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import SqlitePhaseGateRepository
        from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
        from migration_factory.control_tower.application.v2_assistant_model_client import V2AssistantModelResult
        from migration_factory.control_tower.application.v2_model_role_router import V2ModelRole

        conn = sqlite3.connect(str(tmp_path / "rerun.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        apply_pending_migrations(conn)

        class FakeClient:
            def __init__(self):
                self.calls = []
            def answer_with_role(self, *, role, prompt, fallback, output_schema_name=None, require_schema=True):
                self.calls.append(role)
                if role == V2ModelRole.PROPOSER:
                    c = json.dumps({"root_cause":"fix","fix_strategy":"patch","changed_files":["pom.xml"],"proposed_diff":"diff --git a/pom.xml b/pom.xml\n--- a/pom.xml\n+++ b/pom.xml\n@@\n+<dependency><groupId>com.h2database</groupId><artifactId>h2</artifactId><scope>runtime</scope></dependency>\n","risk":"LOW","confidence":0.9,"rationale":"fix","deterministic_rule_id":"DEPENDENCY_ADD_H2_RUNTIME"},sort_keys=True)
                else:
                    c = json.dumps({"decision":"accept","notes":[],"confidence":0.95,"risks":[],"policy_concerns":[],"reviewed_context_checksum":"","reviewed_primary_output_checksum":"","reviewed_diff_checksum":""},sort_keys=True)
                return V2AssistantModelResult(content=c,source="azure",model_status="live_ok",provider="azure",role=role.value,success=True,redacted_summary="ok",failure_reason="")

        gate_repo = SqlitePhaseGateRepository(conn)
        gate_service = V2PhaseGateService(gate_repo)
        repair_gate_service = V2RepairGateService(gate_service=gate_service, max_repair_attempts=3)

        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        legacy = tmp_path / "legacy"
        legacy.mkdir()

        fc = FakeClient()
        result = repair_gate_service.create_next_repair_cycle_from_rerun_failure(
            job_id="job-rerun",
            stage_index=3,
            command_id="cmd-rerun",
            prior_evidence_checksum="sha256:ev1",
            prior_context_checksum="sha256:cp1",
            prior_primary_output_checksum="sha256:po1",
            prior_reviewer_output_checksum="sha256:ro1",
            prior_final_diff_checksum="sha256:fd1",
            prior_policy_validation_checksum="sha256:pv1",
            prior_base_repo_state_checksum="sha256:rs1",
            rerun_result={"errors": ["build failed after repair"], "passed": False},
            rollback_result={"status": "ROLLED_BACK"},
            apply_result={"status": "applied"},
            sandbox_path=str(sandbox),
            run_dir=tmp_path / "run",
            legacy_path=str(legacy),
            deterministic_rule_id="DEPENDENCY_ADD_H2_RUNTIME",
            previous_repair_review_checksums=("sha256:cycle-1",),
            model_client=fc,
            h2_required=True,
        )
        assert result.status == "created"
        assert result.gate_id
        assert len(fc.calls) == 2

    def test_max_attempts_exhausted_creates_terminal_failure(self, tmp_path: Path) -> None:
        import sqlite3
        from migration_factory.control_tower.application.v2_repair_gate_service import V2RepairGateService
        from migration_factory.control_tower.application.v2_phase_gate_service import V2PhaseGateService
        from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import SqlitePhaseGateRepository
        from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations

        conn = sqlite3.connect(str(tmp_path / "rerun2.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        apply_pending_migrations(conn)

        gate_repo = SqlitePhaseGateRepository(conn)
        gate_service = V2PhaseGateService(gate_repo)
        repair_gate_service = V2RepairGateService(gate_service=gate_service, max_repair_attempts=1)

        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        legacy = tmp_path / "legacy"
        legacy.mkdir()

        result = repair_gate_service.create_next_repair_cycle_from_rerun_failure(
            job_id="job-rerun2",
            stage_index=3,
            command_id="cmd-rerun2",
            prior_evidence_checksum="sha256:ev1",
            prior_context_checksum="sha256:cp1",
            prior_primary_output_checksum="sha256:po1",
            prior_reviewer_output_checksum="sha256:ro1",
            prior_final_diff_checksum="sha256:fd1",
            prior_policy_validation_checksum="sha256:pv1",
            prior_base_repo_state_checksum="sha256:rs1",
            rerun_result={"errors": ["test failure"], "passed": False},
            sandbox_path=str(sandbox),
            run_dir=tmp_path / "run",
            legacy_path=str(legacy),
            deterministic_rule_id="DEPENDENCY_ADD_H2_RUNTIME",
            previous_repair_review_checksums=("sha256:cycle-1",),
        )
        assert result.status == "attempts_exhausted"
        assert "exhausted" in result.reason

    def test_no_auto_apply_second_patch(self, tmp_path: Path) -> None:
        import json, sqlite3
        from migration_factory.control_tower.application.v2_repair_gate_service import V2RepairGateService
        from migration_factory.control_tower.application.v2_phase_gate_service import V2PhaseGateService
        from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import SqlitePhaseGateRepository
        from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
        from migration_factory.control_tower.application.v2_assistant_model_client import V2AssistantModelResult
        from migration_factory.control_tower.application.v2_model_role_router import V2ModelRole

        conn = sqlite3.connect(str(tmp_path / "rerun3.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        apply_pending_migrations(conn)

        class FakeClient:
            calls = []
            def answer_with_role(self, *, role, prompt, fallback, output_schema_name=None, require_schema=True):
                self.calls.append(role)
                if role == V2ModelRole.PROPOSER:
                    c = json.dumps({"root_cause":"fix","fix_strategy":"patch","changed_files":["pom.xml"],"proposed_diff":"diff --git a/pom.xml b/pom.xml\n--- a/pom.xml\n+++ b/pom.xml\n@@\n+<dependency><groupId>com.h2database</groupId><artifactId>h2</artifactId><scope>runtime</scope></dependency>\n","risk":"LOW","confidence":0.9,"rationale":"fix","deterministic_rule_id":"DEPENDENCY_ADD_H2_RUNTIME"},sort_keys=True)
                else:
                    c = json.dumps({"decision":"accept","notes":[],"confidence":0.95,"risks":[],"policy_concerns":[],"reviewed_context_checksum":"","reviewed_primary_output_checksum":"","reviewed_diff_checksum":""},sort_keys=True)
                return V2AssistantModelResult(content=c,source="azure",model_status="live_ok",provider="azure",role=role.value,success=True,redacted_summary="ok",failure_reason="")

        gate_repo = SqlitePhaseGateRepository(conn)
        gate_service = V2PhaseGateService(gate_repo)
        repair_gate_service = V2RepairGateService(gate_service=gate_service)

        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        legacy = tmp_path / "legacy"
        legacy.mkdir()

        fc = FakeClient()
        result = repair_gate_service.create_next_repair_cycle_from_rerun_failure(
            job_id="job-rerun3",
            stage_index=3,
            command_id="cmd-rerun3",
            prior_evidence_checksum="sha256:ev1",
            prior_context_checksum="sha256:cp1",
            prior_primary_output_checksum="sha256:po1",
            prior_reviewer_output_checksum="sha256:ro1",
            prior_final_diff_checksum="sha256:fd1",
            prior_policy_validation_checksum="sha256:pv1",
            prior_base_repo_state_checksum="sha256:rs1",
            rerun_result={"errors": ["build failed"], "passed": False},
            sandbox_path=str(sandbox),
            run_dir=tmp_path / "run",
            legacy_path=str(legacy),
            deterministic_rule_id="DEPENDENCY_ADD_H2_RUNTIME",
            previous_repair_review_checksums=("sha256:cycle-1",),
            model_client=fc,
            h2_required=True,
        )
        # A gate was created, but no diff was auto-applied – only reviewed chain produced
        assert result.status == "created"
        assert result.gate_id
        assert len(fc.calls) == 2  # Proposer + Reviewer, no auto-apply

    def test_second_context_includes_previous_cycle_checksums(self, tmp_path: Path) -> None:
        import json, sqlite3
        from migration_factory.control_tower.application.v2_repair_gate_service import V2RepairGateService
        from migration_factory.control_tower.application.v2_phase_gate_service import V2PhaseGateService
        from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import SqlitePhaseGateRepository
        from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
        from migration_factory.control_tower.application.v2_assistant_model_client import V2AssistantModelResult
        from migration_factory.control_tower.application.v2_model_role_router import V2ModelRole

        conn = sqlite3.connect(str(tmp_path / "rerun4.sqlite3"), check_same_thread=False, isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        apply_pending_migrations(conn)

        previous_checksums = ("sha256:prop-1", "sha256:prop-2")

        class FakeClient:
            calls = []
            def answer_with_role(self, *, role, prompt, fallback, output_schema_name=None, require_schema=True):
                self.calls.append({"role": role, "prompt": prompt})
                if role == V2ModelRole.PROPOSER:
                    c = json.dumps({"root_cause":"fix","fix_strategy":"patch","changed_files":["pom.xml"],"proposed_diff":"diff --git a/pom.xml b/pom.xml\n--- a/pom.xml\n+++ b/pom.xml\n@@\n+<dependency><groupId>com.h2database</groupId><artifactId>h2</artifactId><scope>runtime</scope></dependency>\n","risk":"LOW","confidence":0.9,"rationale":"fix","deterministic_rule_id":"DEPENDENCY_ADD_H2_RUNTIME"},sort_keys=True)
                else:
                    c = json.dumps({"decision":"accept","notes":[],"confidence":0.95,"risks":[],"policy_concerns":[],"reviewed_context_checksum":"","reviewed_primary_output_checksum":"","reviewed_diff_checksum":""},sort_keys=True)
                return V2AssistantModelResult(content=c,source="azure",model_status="live_ok",provider="azure",role=role.value,success=True,redacted_summary="ok",failure_reason="")

        gate_repo = SqlitePhaseGateRepository(conn)
        gate_service = V2PhaseGateService(gate_repo)
        repair_gate_service = V2RepairGateService(gate_service=gate_service, max_repair_attempts=5)

        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text("<project/>\n", encoding="utf-8")
        legacy = tmp_path / "legacy"
        legacy.mkdir()

        fc = FakeClient()
        result = repair_gate_service.create_next_repair_cycle_from_rerun_failure(
            job_id="job-rerun4",
            stage_index=3,
            command_id="cmd-rerun4",
            prior_evidence_checksum="sha256:ev1",
            prior_context_checksum="sha256:cp1",
            prior_primary_output_checksum="sha256:po1",
            prior_reviewer_output_checksum="sha256:ro1",
            prior_final_diff_checksum="sha256:fd1",
            prior_policy_validation_checksum="sha256:pv1",
            prior_base_repo_state_checksum="sha256:rs1",
            rerun_result={"errors": ["build failed"], "passed": False},
            sandbox_path=str(sandbox),
            run_dir=tmp_path / "run",
            legacy_path=str(legacy),
            deterministic_rule_id="DEPENDENCY_ADD_H2_RUNTIME",
            previous_repair_review_checksums=previous_checksums,
            model_client=fc,
            h2_required=True,
        )
        assert result.status == "created"
        # Verify prior checksums appear in the proposer prompt via context_pack
        proposer_prompt = fc.calls[0]["prompt"]
        assert any(cs in proposer_prompt for cs in previous_checksums)
