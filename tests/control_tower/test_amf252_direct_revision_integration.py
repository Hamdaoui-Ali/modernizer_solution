"""Integration tests for AMF-252 direct revision fix.

Tests the typed RepairRevisionRequest → create_reviewed_repair_revision()
path with real SQLite, temp filesystem, and fake Proposer/Reviewer.

Also verifies create_reviewed_repair_proposal_on_failure still works
with its failure_payload= kwarg unchanged.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import pytest

from migration_factory.control_tower.application.v2_gate_action_service import (
    V2GateActionService,
)
from migration_factory.control_tower.application.v2_phase_gate_service import (
    V2PhaseGateService,
)
from migration_factory.control_tower.application.v2_repair_flow import (
    V2RepairFlowService,
)
from migration_factory.control_tower.application.v2_repair_gate_service import (
    RepairRevisionRequest,
    ReviewedRepairProposalCreationResult,
    V2RepairGateService,
    _context_pack_from_dict,
)
from migration_factory.control_tower.application.v2_reviewer_service import (
    V2ReviewerService,
)
from migration_factory.control_tower.domain.checksums import sha256_hex, utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
    SqliteControlTowerUnitOfWork,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_artifact_revision_repository import (
    SqliteArtifactRevisionRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_gate_decision_repository import (
    SqliteGateDecisionRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import (
    SqlitePhaseGateRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_repair_repository import (
    V2RepairProposalRecord,
)
from migration_factory.repair_loop.failure_evidence import (
    FailureSource,
    build_failure_evidence,
)
from migration_factory.repair_loop.repair_context import (
    RepairContextPack,
    RepairSourceContext,
    build_repair_context_pack,
    compute_context_pack_checksum,
    context_pack_to_dict,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _connection(tmp_path: Path, name: str = "amf252_integration.sqlite3") -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / name), check_same_thread=False, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    return conn


def _make_evidence(tmp_path: Path) -> tuple[Any, str]:
    evidence = build_failure_evidence(
        failure_source=FailureSource.BUILD,
        job_id="job-amf252",
        stage_index=2,
        command_id="cmd-build-1",
        failure_summary="Compilation failure in H2 migration",
        source_profile="java11",
        target_profile="java17",
        changed_files=("src/main/java/com/example/App.java",),
    )
    evidence_path = tmp_path / "failure_evidence.json"
    evidence_path.write_text(
        json.dumps({
            "failure_source": "build",
            "stage_index": 2,
            "job_id": "job-amf252",
            "command_id": "cmd-build-1",
            "failure_summary": "Compilation failure in H2 migration",
            "source_profile": "java11",
            "target_profile": "java17",
            "changed_files": ["src/main/java/com/example/App.java"],
            "stdout_tail": "",
            "stderr_tail": "",
            "safe_log_preview": "",
            "content_checksum": getattr(evidence, "content_checksum", ""),
            "artifact_checksum": getattr(evidence, "artifact_checksum", ""),
            "created_at": utc_now_text(),
            "schema_version": "1.0.0",
        }, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return evidence, str(evidence_path)


def _make_context_pack(evidence: Any, tmp_path: Path, *, user_comments: str = "") -> tuple[Any, str]:
    context_pack = build_repair_context_pack(
        failure_evidence=evidence,
        job_id=evidence.job_id,
        stage_index=evidence.stage_index,
        command_id=evidence.command_id,
        source_profile=evidence.source_profile,
        target_profile=evidence.target_profile,
        changed_files=evidence.changed_files,
        user_comments=user_comments,
    )
    checksum = compute_context_pack_checksum(context_pack)
    context_pack_with_cs = replace(
        context_pack,
        context_pack_checksum=checksum,
    )
    ctx_path = tmp_path / "repair_context_pack.json"
    ctx_path.write_text(
        json.dumps(context_pack_to_dict(context_pack_with_cs), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return context_pack_with_cs, str(ctx_path)


def _make_diff_file(base_path: Path, name: str = "final.diff") -> str:
    diff_content = (
        "diff --git a/src/main/java/com/example/App.java b/src/main/java/com/example/App.java\n"
        "index e69de29..0000000 100644\n"
        "--- a/src/main/java/com/example/App.java\n"
        "+++ b/src/main/java/com/example/App.java\n"
        "@@ -1,4 +1,4 @@\n"
        " public class App {\n"
        "     public static void main(String[] args) {\n"
        '-        System.out.println("Hello");\n'
        '+        System.out.println("Hello, World!");\n'
        "     }\n"
        "}\n"
    )
    diff_path = base_path if name == "" else base_path / name
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path.write_text(diff_content, encoding="utf-8")
    return str(diff_path)


def _make_git_sandbox(tmp_path: Path) -> Path:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    src_dir = sandbox / "src" / "main" / "java" / "com" / "example"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "App.java").write_text(
        "public class App {\n"
        "    public static void main(String[] args) {\n"
        '        System.out.println("Hello");\n'
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    import subprocess
    subprocess.run(["git", "init"], cwd=str(sandbox), capture_output=True, timeout=30)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(sandbox), capture_output=True, timeout=30)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(sandbox), capture_output=True, timeout=30)
    subprocess.run(["git", "add", "-A"], cwd=str(sandbox), capture_output=True, timeout=30)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(sandbox), capture_output=True, timeout=30)
    return sandbox


def _make_svc_and_conn(tmp_path: Path) -> tuple[V2RepairGateService, sqlite3.Connection, Callable[[], SqliteControlTowerUnitOfWork]]:
    conn = _connection(tmp_path)
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    gate_svc = V2PhaseGateService(gate_repo)
    reviewer_svc = V2ReviewerService()
    repair_svc = V2RepairFlowService(reviewer_service=reviewer_svc)
    action_svc = V2GateActionService(gate_repo, decision_repo, gate_svc, repair_service=repair_svc)
    revision_repo = SqliteArtifactRevisionRepository(conn)
    repair_gate_svc = V2RepairGateService(
        gate_service=gate_svc,
        gate_action_service=action_svc,
        repair_flow=repair_svc,
        revision_repo=revision_repo,
        max_repair_attempts=3,
    )

    def _uow_factory() -> SqliteControlTowerUnitOfWork:
        return SqliteControlTowerUnitOfWork(conn)

    return repair_gate_svc, conn, _uow_factory


def _mock_repair_chain(
    diff_ref: str,
    *,
    reviewer_decision: str = "accept",
    generation_status: str = "ready",
) -> dict[str, Any]:
    diff_checksum = sha256_hex(Path(diff_ref).read_bytes()) if Path(diff_ref).is_file() else ""
    return {
        "review_chain": {
            "reviewer_decision": reviewer_decision,
            "generation_status": generation_status,
            "final_diff_ref": diff_ref,
            "proposed_diff_checksum": diff_checksum,
            "final_diff_source": "reviewer",
            "deterministic_artifact_checksum": "det-cs",
            "context_pack_checksum": "ctx-cs",
            "primary_output_checksum": "primary-cs",
            "reviewer_output_checksum": "reviewer-cs",
            "final_artifact_checksum": "final-cs",
            "proposer_diff_usable": True,
            "reviewer_diff_usable": True,
            "deterministic_validators_pass": True,
            "model_roles": {
                "proposer": {"available": True, "configured_deployment": "fake", "actual_deployment": "fake"},
                "reviewer": {"available": True, "configured_deployment": "fake", "actual_deployment": "fake"},
            },
            "changed_files": ["src/main/java/com/example/App.java"],
            "root_cause": "Missing string concatenation",
            "fix_strategy": "Update print statement",
            "risk": "LOW",
            "policy_validation_checksum": "",
            "final_validation_failures": [],
        },
        "artifact_refs": {
            "deterministic_repair_artifact_ref": str(Path(diff_ref).parent / "deterministic_repair_artifact.json"),
            "primary_repair_llm_output_ref": str(Path(diff_ref).parent / "primary_repair_llm_output.json"),
            "reviewer_repair_llm_output_ref": str(Path(diff_ref).parent / "reviewer_repair_llm_output.json"),
            "final_reviewed_repair_artifact_ref": str(Path(diff_ref).parent / "final_reviewed_repair_artifact.json"),
            "final_reviewed_repair_diff_ref": diff_ref,
            "review_chain_metadata_ref": str(Path(diff_ref).parent / "review_chain.json"),
        },
    }


# ── Tests ─────────────────────────────────────────────────────────────


class TestDirectRepairRevisionIntegration:
    """Integration tests for the direct repair revision path."""

    def setup_method(self) -> None:
        self._produce_patcher = patch(
            "migration_factory.orchestrator.repair_review_chain.produce_repair_review_chain",
        )
        self.mock_produce = self._produce_patcher.start()

    def teardown_method(self) -> None:
        self._produce_patcher.stop()

    def _mock_subprocess_run(self) -> Any:
        """Return a context manager that patches subprocess.run for the service call."""
        return patch(
            "migration_factory.control_tower.application.v2_repair_gate_service.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["git", "apply", "--check", "dummy"],
                returncode=0,
                stdout="",
                stderr="",
            ),
        )

    def test_create_reviewed_repair_revision_success(self, tmp_path: Path) -> None:
        """Happy path: RepairRevisionRequest → create_reviewed_repair_revision → created result."""
        service, conn, uow_factory = _make_svc_and_conn(tmp_path)
        sandbox = _make_git_sandbox(tmp_path)
        run_dir = tmp_path / "run_dir"
        run_dir.mkdir(parents=True, exist_ok=True)
        output_dir = tmp_path / "repair_chain" / "proposal_test"
        output_dir.mkdir(parents=True, exist_ok=True)

        evidence, evidence_ref = _make_evidence(tmp_path)
        context_pack, context_ref = _make_context_pack(evidence, tmp_path)
        diff_ref = _make_diff_file(output_dir)

        self.mock_produce.return_value = _mock_repair_chain(diff_ref, reviewer_decision="accept")

        request = RepairRevisionRequest(
            job_id="job-amf252",
            stage_index=2,
            command_id="cmd-build-1",
            failure_evidence_ref=evidence_ref,
            repair_context_ref=context_ref,
            run_dir=run_dir,
            sandbox_path=sandbox,
            legacy_path=None,
            source_profile="java11",
            target_profile="java17",
            validation_context_ref="",
            validation_context_checksum="",
            source_proposal_id="original-proposal-1",
            revision_of="original-proposal-1",
            revision_number=2,
            output_dir=output_dir,
        )

        with self._mock_subprocess_run():
            result = service.create_reviewed_repair_revision(
                request=request,
                model_client=None,
                uow_factory=uow_factory,
            )

        assert isinstance(result, ReviewedRepairProposalCreationResult)
        assert result.status == "created", f"Expected 'created', got '{result.status}': {result.reason}"
        assert result.proposal_id, "Expected non-empty proposal_id"
        assert result.diff_checksum, "Expected non-empty diff_checksum"
        assert result.reviewer_decision == "accept"
        assert result.attempt_number == 1
        assert result.remaining_attempts == 2
        assert result.final_diff_source == "reviewer"
        assert result.generation_status == "ready"

        proposals = list(conn.execute("SELECT * FROM v2_repair_proposals WHERE proposal_id = ?", (result.proposal_id,)))
        assert len(proposals) == 1, "Expected exactly one proposal record in DB"
        row = proposals[0]
        assert row["status"] == "user_review_required"
        assert row["job_id"] == "job-amf252"

    def test_create_reviewed_repair_revision_missing_evidence(self, tmp_path: Path) -> None:
        """Missing failure_evidence_ref returns skipped."""
        service, conn, uow_factory = _make_svc_and_conn(tmp_path)
        sandbox = _make_git_sandbox(tmp_path)
        run_dir = tmp_path / "run_dir2"
        run_dir.mkdir()
        output_dir = tmp_path / "repair_chain2" / "proposal_missing"
        output_dir.mkdir(parents=True)

        evidence, evidence_ref = _make_evidence(tmp_path)
        context_pack, context_ref = _make_context_pack(evidence, tmp_path)

        request = RepairRevisionRequest(
            job_id="job-amf252",
            stage_index=2,
            command_id="cmd-build-1",
            failure_evidence_ref=str(tmp_path / "nonexistent_evidence.json"),
            repair_context_ref=context_ref,
            run_dir=run_dir,
            sandbox_path=sandbox,
            legacy_path=None,
            source_profile="java11",
            target_profile="java17",
            validation_context_ref="",
            validation_context_checksum="",
            source_proposal_id="orig-2",
            revision_of="orig-2",
            revision_number=1,
            output_dir=output_dir,
        )

        result = service.create_reviewed_repair_revision(
            request=request,
            model_client=None,
            uow_factory=uow_factory,
        )

        assert isinstance(result, ReviewedRepairProposalCreationResult)
        assert result.status == "skipped"
        assert "not found" in result.reason.lower()

    def test_create_reviewed_repair_revision_exhausted_attempts(self, tmp_path: Path) -> None:
        """Exceeding max_repair_attempts returns attempts_exhausted."""
        service, conn, uow_factory = _make_svc_and_conn(tmp_path)
        sandbox = _make_git_sandbox(tmp_path)
        run_dir = tmp_path / "run_dir3"
        run_dir.mkdir()
        output_dir = tmp_path / "repair_chain3"
        output_dir.mkdir(parents=True)

        evidence, evidence_ref = _make_evidence(tmp_path)
        context_pack, context_ref = _make_context_pack(evidence, tmp_path)

        service._attempt_counts[("job-amf252", 2)] = 999

        diff_ref = _make_diff_file(output_dir)
        self.mock_produce.return_value = _mock_repair_chain(diff_ref)

        request = RepairRevisionRequest(
            job_id="job-amf252",
            stage_index=2,
            command_id="cmd-build-1",
            failure_evidence_ref=evidence_ref,
            repair_context_ref=context_ref,
            run_dir=run_dir,
            sandbox_path=sandbox,
            legacy_path=None,
            source_profile="java11",
            target_profile="java17",
            validation_context_ref="",
            validation_context_checksum="",
            source_proposal_id="orig-3",
            revision_of="orig-3",
            revision_number=1,
            output_dir=output_dir,
        )

        result = service.create_reviewed_repair_revision(request=request, model_client=None, uow_factory=uow_factory)

        assert isinstance(result, ReviewedRepairProposalCreationResult)
        assert result.status == "attempts_exhausted", f"Expected 'attempts_exhausted', got '{result.status}'"
        assert result.remaining_attempts == 0

    def test_on_failure_still_works_with_failure_payload(self, tmp_path: Path) -> None:
        """create_reviewed_repair_proposal_on_failure still accepts failure_payload= kwarg."""
        service, conn, uow_factory = _make_svc_and_conn(tmp_path)
        run_dir = tmp_path / "run_dir_of"
        run_dir.mkdir()
        output_dir = run_dir / "repair_chain"
        output_dir.mkdir(parents=True)

        evidence, evidence_ref = _make_evidence(tmp_path)
        context_pack, context_ref = _make_context_pack(evidence, tmp_path)
        diff_ref = _make_diff_file(output_dir)

        self.mock_produce.return_value = _mock_repair_chain(diff_ref)

        failure_payload: dict[str, Any] = {
            "_repair_failure_evidence_ref": evidence_ref,
            "_repair_context_pack_ref": context_ref,
            "_repair_run_dir": str(run_dir),
            "_repair_sandbox_path": str(_make_git_sandbox(tmp_path / "sandbox_of")),
            "legacy_path": "",
            "source_profile": "java11",
            "target_profile": "java17",
            "_repair_validation_context_ref": "",
            "_repair_validation_context_checksum": "",
            "command_id": "cmd-of-1",
        }

        with self._mock_subprocess_run():
            result = service.create_reviewed_repair_proposal_on_failure(
                job_id="job-amf252-of",
                stage_index=2,
                command_id="cmd-of-1",
                failure_payload=failure_payload,
                model_client=None,
                uow_factory=uow_factory,
            )

        assert isinstance(result, ReviewedRepairProposalCreationResult)
        assert result.status == "created", f"Expected 'created' via failure_payload, got '{result.status}': {result.reason}"
        assert result.proposal_id, "Expected non-empty proposal_id"

    def test_on_failure_missing_refs_returns_skipped(self, tmp_path: Path) -> None:
        """create_reviewed_repair_proposal_on_failure with missing refs returns skipped."""
        service, conn, uow_factory = _make_svc_and_conn(tmp_path)

        result = service.create_reviewed_repair_proposal_on_failure(
            job_id="job-missing",
            stage_index=0,
            command_id="cmd-missing",
            failure_payload={"_repair_failure_evidence_ref": "", "_repair_context_pack_ref": ""},
            model_client=None,
            uow_factory=uow_factory,
        )

        assert isinstance(result, ReviewedRepairProposalCreationResult)
        assert result.status == "skipped"
        assert "missing" in result.reason.lower()

    def test_create_reviewed_repair_revision_typed_request_is_frozen(self) -> None:
        """RepairRevisionRequest is a frozen dataclass."""
        request = RepairRevisionRequest(
            job_id="test",
            stage_index=1,
            command_id="cmd",
            failure_evidence_ref="fe",
            repair_context_ref="rc",
            run_dir=Path("/tmp"),
            sandbox_path=Path("/tmp"),
            legacy_path=None,
            source_profile="sp",
            target_profile="tp",
            validation_context_ref="vc",
            validation_context_checksum="vcc",
            source_proposal_id="sp",
            revision_of="ro",
            revision_number=1,
            output_dir=Path("/tmp/out"),
        )
        with pytest.raises(Exception):
            request.job_id = "cannot-change"

    def test_create_reviewed_repair_revision_passes_source_proposal_id(self, tmp_path: Path) -> None:
        """source_proposal_id and revision_of appear in the lineage manifest."""
        service, conn, uow_factory = _make_svc_and_conn(tmp_path)
        sandbox = _make_git_sandbox(tmp_path)
        run_dir = tmp_path / "run_dir_sp"
        run_dir.mkdir()
        output_dir = tmp_path / "repair_chain_sp" / "proposal_sp"
        output_dir.mkdir(parents=True)

        evidence, evidence_ref = _make_evidence(tmp_path)
        context_pack, context_ref = _make_context_pack(evidence, tmp_path)
        diff_ref = _make_diff_file(output_dir)

        self.mock_produce.return_value = _mock_repair_chain(diff_ref)

        request = RepairRevisionRequest(
            job_id="job-sp",
            stage_index=1,
            command_id="cmd-sp",
            failure_evidence_ref=evidence_ref,
            repair_context_ref=context_ref,
            run_dir=run_dir,
            sandbox_path=sandbox,
            legacy_path=None,
            source_profile="java11",
            target_profile="java17",
            validation_context_ref="",
            validation_context_checksum="",
            source_proposal_id="original-source-1",
            revision_of="original-revision-of-1",
            revision_number=3,
            output_dir=output_dir,
        )

        with self._mock_subprocess_run():
            result = service.create_reviewed_repair_revision(request=request, model_client=None, uow_factory=uow_factory)

        assert isinstance(result, ReviewedRepairProposalCreationResult)
        assert result.status == "created", f"Expected 'created', got '{result.status}': {result.reason}"

        proposals = list(conn.execute("SELECT * FROM v2_repair_proposals WHERE proposal_id = ?", (result.proposal_id,)))
        assert len(proposals) == 1
        row = proposals[0]

        lineage_manifest_ref = row["lineage_manifest_ref"]
        assert lineage_manifest_ref
        lineage = json.loads(Path(lineage_manifest_ref).read_text(encoding="utf-8"))
        assert lineage["source_proposal_id"] == "original-source-1"
        assert lineage["revision_of"] == "original-revision-of-1"
        assert lineage["revision_number"] == 3

    def test_context_pack_typed_round_trip(self, tmp_path: Path) -> None:
        """Prove that RepairSourceContext types survive replace-based checksum assignment.

        The original defective code used RepairContextPack(**context_pack_to_dict(...))
        which flattened RepairSourceContext into plain dicts. dataclasses.replace
        preserves the nested types.
        """
        evidence, _ = _make_evidence(tmp_path)
        source_ctx = RepairSourceContext(
            path="src/main/java/com/example/App.java",
            content_checksum="abc123",
            source_file_sha256="abc123",
            context_excerpt_sha256="def456",
            content="public class App { }",
            start_line=1,
            end_line=5,
            reason_included="test",
            context_is_complete=True,
        )
        context_pack = build_repair_context_pack(
            failure_evidence=evidence,
            job_id=evidence.job_id,
            stage_index=evidence.stage_index,
            command_id=evidence.command_id,
            source_profile=evidence.source_profile,
            target_profile=evidence.target_profile,
            changed_files=evidence.changed_files,
            source_contexts=(source_ctx,),
        )

        for sc in context_pack.source_contexts:
            assert isinstance(sc, RepairSourceContext)

        checksum = compute_context_pack_checksum(context_pack)
        revised_pack = replace(context_pack, context_pack_checksum=checksum)

        assert revised_pack.context_pack_checksum == checksum

        for sc in revised_pack.source_contexts:
            assert isinstance(sc, RepairSourceContext)

        as_dict = context_pack_to_dict(revised_pack)
        assert isinstance(as_dict, dict)

        json_str = json.dumps(as_dict, sort_keys=True, indent=2)
        parsed = json.loads(json_str)
        assert "source_contexts" in parsed
        assert len(parsed["source_contexts"]) > 0
        assert "path" in parsed["source_contexts"][0]

        reloaded = _context_pack_from_dict(parsed)
        for sc in reloaded.source_contexts:
            assert isinstance(sc, RepairSourceContext)

    def test_direct_revision_full_integration(self, tmp_path: Path) -> None:
        """Full integration: first proposal + revision with real SQLite/filesystem.

        REQUEST_REVISION -> Proposer called once -> Reviewer called once
        -> one new proposal persisted. New proposal's revision_of points to
        old proposal. New checksum differs from old. Old proposal unchanged.
        No automatic Apply.
        """
        service, conn, uow_factory = _make_svc_and_conn(tmp_path)
        sandbox = _make_git_sandbox(tmp_path)
        run_dir = tmp_path / "run_dir"
        run_dir.mkdir(parents=True, exist_ok=True)
        output_dir1 = tmp_path / "repair_chain" / "proposal_old"
        output_dir1.mkdir(parents=True, exist_ok=True)

        evidence, evidence_ref = _make_evidence(tmp_path)
        context_pack, context_ref = _make_context_pack(evidence, tmp_path)
        diff_ref1 = _make_diff_file(output_dir1, name="old_diff.diff")

        self.mock_produce.return_value = _mock_repair_chain(diff_ref1, reviewer_decision="accept")

        request1 = RepairRevisionRequest(
            job_id="job-full-int",
            stage_index=2,
            command_id="cmd-original",
            failure_evidence_ref=evidence_ref,
            repair_context_ref=context_ref,
            run_dir=run_dir,
            sandbox_path=sandbox,
            legacy_path=None,
            source_profile="java11",
            target_profile="java17",
            validation_context_ref="",
            validation_context_checksum="",
            source_proposal_id="",
            revision_of="",
            revision_number=1,
            output_dir=output_dir1,
        )

        with self._mock_subprocess_run():
            result1 = service.create_reviewed_repair_revision(
                request=request1,
                model_client=None,
                uow_factory=uow_factory,
            )

        assert result1.status == "created"
        old_proposal_id = result1.proposal_id
        old_diff_checksum = result1.diff_checksum
        assert old_proposal_id
        assert old_diff_checksum

        old_call_count = self.mock_produce.call_count
        assert old_call_count == 1

        output_dir2 = tmp_path / "repair_chain" / "proposal_rev"
        output_dir2.mkdir(parents=True, exist_ok=True)
        diff_ref2 = output_dir2 / "revised_diff.diff"
        revised_diff_content = (
            "diff --git a/src/main/java/com/example/App.java b/src/main/java/com/example/App.java\n"
            "index e69de29..0000000 100644\n"
            "--- a/src/main/java/com/example/App.java\n"
            "+++ b/src/main/java/com/example/App.java\n"
            "@@ -1,4 +1,5 @@\n"
            " public class App {\n"
            "     public static void main(String[] args) {\n"
            '-        System.out.println("Hello");\n'
            '+        System.out.println("Hello, World!");\n'
            '+        System.out.println("Goodbye!");\n'
            "     }\n"
            "}\n"
        )
        diff_ref2.write_text(revised_diff_content, encoding="utf-8")

        self.mock_produce.return_value = _mock_repair_chain(str(diff_ref2), reviewer_decision="accept")

        request2 = RepairRevisionRequest(
            job_id="job-full-int",
            stage_index=2,
            command_id="cmd-revision",
            failure_evidence_ref=evidence_ref,
            repair_context_ref=context_ref,
            run_dir=run_dir,
            sandbox_path=sandbox,
            legacy_path=None,
            source_profile="java11",
            target_profile="java17",
            validation_context_ref="",
            validation_context_checksum="",
            source_proposal_id=old_proposal_id,
            revision_of=old_proposal_id,
            revision_number=2,
            output_dir=output_dir2,
        )

        with self._mock_subprocess_run():
            result2 = service.create_reviewed_repair_revision(
                request=request2,
                model_client=None,
                uow_factory=uow_factory,
            )

        assert result2.status == "created", f"Expected 'created', got '{result2.status}': {result2.reason}"
        new_proposal_id = result2.proposal_id
        new_diff_checksum = result2.diff_checksum

        assert self.mock_produce.call_count == old_call_count + 1

        new_rows = list(conn.execute(
            "SELECT * FROM v2_repair_proposals WHERE proposal_id = ?", (new_proposal_id,)
        ))
        assert len(new_rows) == 1
        new_row = new_rows[0]
        assert new_row["revision_of"] == old_proposal_id

        assert new_diff_checksum != old_diff_checksum

        old_rows = list(conn.execute(
            "SELECT * FROM v2_repair_proposals WHERE proposal_id = ?", (old_proposal_id,)
        ))
        assert len(old_rows) == 1
        old_row = old_rows[0]
        assert old_row["diff_checksum"] == old_diff_checksum
        assert old_row["revision_of"] is None or old_row["revision_of"] == ""

        assert old_row["apply_claim_status"] is None or old_row["apply_claim_status"] == ""
        assert new_row["apply_claim_status"] is None or new_row["apply_claim_status"] == ""

    def test_revision_same_command_id_does_not_conflict(self, tmp_path: Path) -> None:
        """Revision with same command_id as original must not be rejected.

        The conflict check in _create_reviewed_repair_proposal_from_refs
        excludes the source_proposal_id when checking for active proposals
        with the same command_id. This test proves the first revision of
        a user_review_required proposal succeeds even with identical command_id.
        """
        service, conn, uow_factory = _make_svc_and_conn(tmp_path)
        sandbox = _make_git_sandbox(tmp_path)
        run_dir = tmp_path / "run_dir_same_cmd"
        run_dir.mkdir(parents=True, exist_ok=True)
        output_dir1 = tmp_path / "repair_chain_same" / "proposal_orig"
        output_dir1.mkdir(parents=True, exist_ok=True)

        evidence, evidence_ref = _make_evidence(tmp_path)
        context_pack, context_ref = _make_context_pack(evidence, tmp_path)
        diff_ref1 = _make_diff_file(output_dir1, name="orig.diff")

        self.mock_produce.return_value = _mock_repair_chain(diff_ref1, reviewer_decision="accept")

        request1 = RepairRevisionRequest(
            job_id="job-same-cmd",
            stage_index=2,
            command_id="cmd-same",
            failure_evidence_ref=evidence_ref,
            repair_context_ref=context_ref,
            run_dir=run_dir,
            sandbox_path=sandbox,
            legacy_path=None,
            source_profile="java11",
            target_profile="java17",
            validation_context_ref="",
            validation_context_checksum="",
            source_proposal_id="",
            revision_of="",
            revision_number=1,
            output_dir=output_dir1,
        )

        with self._mock_subprocess_run():
            result1 = service.create_reviewed_repair_revision(
                request=request1, model_client=None, uow_factory=uow_factory,
            )

        assert result1.status == "created"
        old_id = result1.proposal_id

        output_dir2 = tmp_path / "repair_chain_same" / "proposal_rev"
        output_dir2.mkdir(parents=True, exist_ok=True)
        diff_ref2 = output_dir2 / "revised.diff"
        diff_ref2.write_text(
            "diff --git a/src/main/java/com/example/App.java "
            "b/src/main/java/com/example/App.java\n"
            "index e69de29..0000000 100644\n"
            "--- a/src/main/java/com/example/App.java\n"
            "+++ b/src/main/java/com/example/App.java\n"
            "@@ -1,4 +1,5 @@\n"
            " public class App {\n"
            "     public static void main(String[] args) {\n"
            '-        System.out.println("Hello");\n'
            '+        System.out.println("Hello, World!");\n'
            "     }\n"
            "}\n",
            encoding="utf-8",
        )

        self.mock_produce.return_value = _mock_repair_chain(str(diff_ref2), reviewer_decision="accept")

        request2 = RepairRevisionRequest(
            job_id="job-same-cmd",
            stage_index=2,
            command_id="cmd-same",
            failure_evidence_ref=evidence_ref,
            repair_context_ref=context_ref,
            run_dir=run_dir,
            sandbox_path=sandbox,
            legacy_path=None,
            source_profile="java11",
            target_profile="java17",
            validation_context_ref="",
            validation_context_checksum="",
            source_proposal_id=old_id,
            revision_of=old_id,
            revision_number=2,
            output_dir=output_dir2,
        )

        with self._mock_subprocess_run():
            result2 = service.create_reviewed_repair_revision(
                request=request2, model_client=None, uow_factory=uow_factory,
            )

        assert result2.status == "created", (
            f"Expected 'created' with same command_id, got '{result2.status}': {result2.reason}"
        )
        assert result2.proposal_id != old_id

        proposals = list(conn.execute(
            "SELECT * FROM v2_repair_proposals WHERE proposal_id = ?",
            (result2.proposal_id,),
        ))
        assert len(proposals) == 1
        assert proposals[0]["revision_of"] == old_id
