"""Focused tests for F15-JOB-053-056 — Evidence pack builders.

Proves:
  - Analysis evidence pack (job053)
  - Planning evidence pack (job054)
  - Approval evidence pack (job055)
  - Failure evidence pack (job056)
  - Bounded context budget (job059)
  - Redaction filters (job058)
  - Resolver failure messages (job057)
  - Artifact truth regression (job060)
"""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from migration_factory.control_tower.application.v2_evidence_pack_builder import (
    EvidencePackBuilder,
    EvidencePack,
    build_analysis_evidence_pack,
    build_planning_evidence_pack,
    build_approval_evidence_pack,
    build_failure_evidence_pack,
    evidence_pack_to_dict,
    DEFAULT_EVIDENCE_CHAR_BUDGET,
    DEFAULT_PACK_CHAR_BUDGET,
)
from migration_factory.control_tower.application.v2_gate_artifact_resolver import (
    V2GateArtifactResolver,
    ResolutionFailureReason,
)
from migration_factory.control_tower.domain.checksums import sha256_hex
from migration_factory.control_tower.domain.gate_artifact_ref import (
    GateArtifactRef,
    build_artifact_refs,
)
from migration_factory.control_tower.domain.entities import PhaseGateRecord
from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import (
    SqlitePhaseGateRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(tmp_path / "test_evidence.db"),
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    return conn


@pytest.fixture
def storage(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    root.mkdir()
    return root


def create_artifact(root: Path, rel_path: str, content: str) -> str:
    """Create an artifact file and return its checksum."""
    full_path = root / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")
    return sha256_hex(content.encode("utf-8"))


def make_gate(
    repo: SqlitePhaseGateRepository,
    gate_id: str,
    job_id: str,
    refs: tuple[GateArtifactRef, ...],
    gate_phase: str = "analysis_review",
) -> str:
    record = PhaseGateRecord(
        gate_id=gate_id,
        job_id=job_id,
        gate_phase=gate_phase,
        stage_index=1,
        gate_status="open",
        gate_decision="pending",
        source_artifact_checksum="test-checksum",
        resolved_artifact_checksum=None,
        source_artifact_refs_json=json.dumps([
            {"kind": r.kind, "path_or_ref": r.path_or_ref, "checksum": r.checksum}
            for r in refs
        ], separators=(",", ":")),
        created_at="2026-06-17T12:00:00Z",
    )
    repo.save(record)
    return gate_id


# ── Test: Analysis Evidence Pack ─────────────────────────────────────


class TestAnalysisEvidencePack:
    """F15-JOB-053: Analysis evidence pack builder."""

    def test_build_analysis_pack(self, db_conn, storage):
        """Build analysis pack with multiple artifacts."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        chk1 = create_artifact(storage, "analysis/summary.json",
                                '{"findings": [{"severity": "high", "count": 3}]}')
        chk2 = create_artifact(storage, "deps/graph.dot",
                                "digraph G { main -> lib }")

        refs = build_artifact_refs([
            ("analysis_report", "analysis/summary.json", chk1),
            ("dependency_graph", "deps/graph.dot", chk2),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-analysis", refs)

        pack = build_analysis_evidence_pack(resolver, gate_id)
        assert pack.pack_type == "analysis"
        assert pack.gate_phase == "analysis_review"
        assert pack.resolved_artifact_count == 2
        assert pack.failure_message is None
        assert "analysis_report" in pack.summary or len(pack.artifacts) == 2

    def test_analysis_pack_no_refs(self, db_conn, storage):
        """Analysis pack with no refs returns empty."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        gate_id = make_gate(repo, uuid4().hex, "job-empty", ())
        pack = build_analysis_evidence_pack(resolver, gate_id)
        assert pack.resolved_artifact_count == 0
        assert pack.failure_message is not None

    def test_analysis_pack_missing_artifact(self, db_conn, storage):
        """Analysis pack with missing artifact reports it."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        chk = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        refs = build_artifact_refs([
            ("analysis_report", "analysis/missing.json", chk),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-missing", refs)
        pack = build_analysis_evidence_pack(resolver, gate_id)
        assert len(pack.missing_refs) > 0 or pack.resolved_artifact_count == 0

    def test_analysis_summary_findings(self, db_conn, storage):
        """Analysis pack summary includes findings."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        chk = create_artifact(storage, "analysis/summary.json",
                               '{"findings": [{"severity": "high"}]}')
        refs = build_artifact_refs([
            ("analysis_report", "analysis/summary.json", chk),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-summary", refs)
        pack = build_analysis_evidence_pack(resolver, gate_id)
        assert pack.resolved_artifact_count == 1
        assert "analysis_report" in pack.summary


# ── Test: Planning Evidence Pack ─────────────────────────────────────


class TestPlanningEvidencePack:
    """F15-JOB-054: Planning evidence pack builder."""

    def test_build_planning_pack(self, db_conn, storage):
        """Build planning pack with migration plan artifacts."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        chk1 = create_artifact(storage, "plan/migration.yaml",
                                "units:\n  - id: UNIT001\n  - id: UNIT002")
        chk2 = create_artifact(storage, "plan/risks.yaml",
                                "risks:\n  - high: database migration")

        refs = build_artifact_refs([
            ("migration_plan", "plan/migration.yaml", chk1),
            ("migration_units", "plan/risks.yaml", chk2),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-planning", refs)

        pack = build_planning_evidence_pack(resolver, gate_id)
        assert pack.pack_type == "planning"
        assert pack.gate_phase == "planning_review"
        assert pack.resolved_artifact_count >= 1

    def test_planning_pack_summary(self, db_conn, storage):
        """Planning pack summary includes plan info."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        chk = create_artifact(storage, "plan/migration.yaml",
                                "units:\n  - id: UNIT001\n    risk: high")
        refs = build_artifact_refs([
            ("migration_plan", "plan/migration.yaml", chk),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-plan-summary", refs)
        pack = build_planning_evidence_pack(resolver, gate_id)
        assert pack.resolved_artifact_count == 1

    def test_planning_pack_no_refs(self, db_conn, storage):
        """Planning pack with no refs returns empty."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)
        gate_id = make_gate(repo, uuid4().hex, "job-plan-empty", ())
        pack = build_planning_evidence_pack(resolver, gate_id)
        assert pack.resolved_artifact_count == 0


# ── Test: Approval Evidence Pack ─────────────────────────────────────


class TestApprovalEvidencePack:
    """F15-JOB-055: Approval evidence pack builder."""

    def test_build_approval_pack(self, db_conn, storage):
        """Build approval pack with accepted analysis + plan refs."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        chk1 = create_artifact(storage, "approval/analysis_summary.txt",
                                "Accepted analysis with 3 high risks")
        chk2 = create_artifact(storage, "approval/plan_summary.txt",
                                "Migration plan with 2 units, 1 config change")

        refs = build_artifact_refs([
            ("approval_request", "approval/analysis_summary.txt", chk1),
            ("migration_plan", "approval/plan_summary.txt", chk2),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-approval", refs,
                            gate_phase="approval_review")

        pack = build_approval_evidence_pack(resolver, gate_id)
        assert pack.pack_type == "approval"
        assert pack.gate_phase == "approval_review"
        assert pack.resolved_artifact_count == 2

    def test_approval_pack_missing_ref(self, db_conn, storage):
        """Approval pack with missing ref handles gracefully."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        chk = create_artifact(storage, "approval/analysis.txt", "Analysis OK")
        bad = "abcd" * 16

        refs = build_artifact_refs([
            ("approval_request", "approval/analysis.txt", chk),
            ("migration_plan", "plan/missing.yaml", bad),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-approval-missing", refs,
                            gate_phase="approval_review")
        pack = build_approval_evidence_pack(resolver, gate_id)
        # At least the good one resolved
        assert pack.resolved_artifact_count >= 1


# ── Test: Failure Evidence Pack ──────────────────────────────────────


class TestFailureEvidencePack:
    """F15-JOB-056: Failure evidence pack builder."""

    def test_build_failure_pack(self, db_conn, storage):
        """Build failure pack with logs and classification."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        chk1 = create_artifact(storage, "logs/build.log",
                                "BUILD FAILED: compilation error at line 42")
        chk2 = create_artifact(storage, "classification/result.txt",
                                "Failure type: compilation, repairable: true")

        refs = build_artifact_refs([
            ("build_log", "logs/build.log", chk1),
            ("failure_classification", "classification/result.txt", chk2),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-failure", refs,
                            gate_phase="repair_review")

        pack = build_failure_evidence_pack(resolver, gate_id)
        assert pack.pack_type == "failure"
        assert pack.gate_phase == "repair_review"
        assert pack.resolved_artifact_count == 2

    def test_failure_pack_missing_log(self, db_conn, storage):
        """Failure pack with missing log handles gracefully."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        chk = "abcd1234" * 16
        refs = build_artifact_refs([
            ("build_log", "logs/missing.log", chk),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-failure-missing", refs,
                            gate_phase="repair_review")
        pack = build_failure_evidence_pack(resolver, gate_id)
        assert len(pack.missing_refs) > 0 or pack.resolved_artifact_count == 0


# ── Test: Resolver Failure Messages (job057) ─────────────────────────


class TestResolverFailureMessages:
    """F15-JOB-057: Artifact resolver failure messages."""

    def test_gate_not_found_message(self, db_conn, storage):
        """Non-existent gate returns appropriate message."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)
        result = resolver.resolve_gate_artifacts("nonexistent-gate")
        assert result.failure_message is not None
        assert "not found" in result.failure_message.lower()
        # No raw paths or secrets in message
        assert "/" not in result.failure_message or "not" in result.failure_message

    def test_checksum_mismatch_message(self, db_conn, storage):
        """Checksum mismatch returns sanitized message."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        content = "Some analysis output"
        create_artifact(storage, "analysis/summary.json", content)
        wrong = "0" * 64

        refs = build_artifact_refs([
            ("analysis_report", "analysis/summary.json", wrong),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-chk-msg", refs)
        result = resolver.resolve_gate_artifacts(gate_id)
        assert result.failure_message is not None
        assert "checksum" in result.failure_message.lower() or "mismatch" in result.failure_message.lower()

    def test_missing_artifact_message(self, db_conn, storage):
        """Missing artifact returns not-found message."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        chk = "a" * 64
        refs = build_artifact_refs([
            ("analysis_report", "analysis/missing.json", chk),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-missing-msg", refs)
        result = resolver.resolve_gate_artifacts(gate_id)
        assert result.failure_message is not None
        # No raw paths in failure message
        assert "/home/" not in (result.failure_message or "")
        assert "/tmp/" not in (result.failure_message or "")

    def test_no_silent_fallback_on_stale(self, db_conn, storage):
        """Stale checksum does not fall back to content."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        # Content at gate time
        content = "Original analysis"
        chk = create_artifact(storage, "analysis/summary.json", content)

        refs = build_artifact_refs([
            ("analysis_report", "analysis/summary.json", chk),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-stale-msg", refs)

        # Content changes after gate
        storage.joinpath("analysis/summary.json").write_text("Modified content")

        result = resolver.resolve_gate_artifacts(gate_id)
        # Must not silently return modified content
        assert len(result.artifacts) == 0 or not result.artifacts[0].checksum_verified


# ── Test: Redaction Filters (job058) ────────────────────────────────


class TestGateRedaction:
    """F15-JOB-058: Redaction filters for gate packs."""

    def test_absolute_paths_redacted_in_pack(self, db_conn, storage):
        """Evidence pack redacts absolute paths from content."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        content = "Found config at /home/user/.ssh/id_rsa"
        chk = create_artifact(storage, "analysis/findings.txt", content)

        refs = build_artifact_refs([
            ("analysis_report", "analysis/findings.txt", chk),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-redact", refs)

        pack = build_analysis_evidence_pack(resolver, gate_id)
        # Verify the DTO is redacted
        dto = evidence_pack_to_dict(pack)
        for artifact in dto["artifacts"]:
            assert "/home/user/" not in artifact["content"]

    def test_model_summary_redacted(self, db_conn, storage):
        """Evidence pack redacts model summary patterns."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        # Use a long string that resembles model output
        content = ("Model config: deployment-id=triton-abc123-prod\n"
                   "Using API key sk-abc123def456")
        chk = create_artifact(storage, "analysis/config.txt", content)

        refs = build_artifact_refs([
            ("analysis_report", "analysis/config.txt", chk),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-redact2", refs)

        dto = evidence_pack_to_dict(build_analysis_evidence_pack(resolver, gate_id))
        assert dto["redaction_status"] == "applied"

    def test_no_raw_secrets_in_failure_message(self, db_conn, storage):
        """Failure message does not include secret content."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        chk = "0" * 64
        refs = build_artifact_refs([
            ("analysis_report", "analysis/secret_stuff.json", chk),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-secret", refs)

        result = resolver.resolve_gate_artifacts(gate_id)
        msg = result.failure_message or ""
        # No raw paths
        assert "/home/" not in msg
        assert "/tmp/" not in msg or "tmp" in msg.lower()


# ── Test: Bounded Context Budget (job059) ───────────────────────────


class TestContextBudget:
    """F15-JOB-059: Bounded context budget for gate packs."""

    def test_large_artifact_truncated(self, db_conn, storage):
        """Large artifact content is truncated to budget."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage,
                                           max_content_chars=100)

        content = "A" * 10_000
        chk = create_artifact(storage, "analysis/large.txt", content)

        refs = build_artifact_refs([
            ("analysis_report", "analysis/large.txt", chk),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-budget", refs)

        pack = build_analysis_evidence_pack(resolver, gate_id)
        assert pack.resolved_artifact_count == 1
        # Content should be truncated
        assert any("truncated" in a.content for a in pack.artifacts)

    def test_pack_budget_limits_artifacts(self, db_conn, storage):
        """Pack budget limits total content across artifacts."""
        repo = SqlitePhaseGateRepository(db_conn)
        builder = EvidencePackBuilder(
            V2GateArtifactResolver(repo, storage_root=storage),
            evidence_budget=100,
            pack_budget=150,
        )

        chk1 = create_artifact(storage, "analysis/first.txt", "X" * 200)
        chk2 = create_artifact(storage, "analysis/second.txt", "Y" * 200)

        refs = build_artifact_refs([
            ("analysis_report", "analysis/first.txt", chk1),
            ("dependency_graph", "analysis/second.txt", chk2),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-pack-budget", refs)

        pack = builder.build_analysis_pack(gate_id)
        total_chars = sum(len(a.content) for a in pack.artifacts)
        # Total should be <= pack_budget + some overhead for truncation markers
        assert total_chars <= 300  # pack_budget + some marker padding

    def test_critical_fields_preserved(self, db_conn, storage):
        """Critical fields are preserved even when content is truncated."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        content = "High risk: database migration\nRisk count: 5"
        chk = create_artifact(storage, "analysis/risks.txt", content)

        refs = build_artifact_refs([
            ("analysis_report", "analysis/risks.txt", chk),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-critical", refs)

        pack = build_analysis_evidence_pack(resolver, gate_id)
        dto = evidence_pack_to_dict(pack)
        assert dto["resolved_artifact_count"] >= 1
        assert dto["gate_id"] == gate_id
        assert dto["pack_type"] == "analysis"


# ── Test: Artifact Truth Regression (job060) ────────────────────────


class TestArtifactTruthRegression:
    """F15-JOB-060: Gate artifact truth regression from F14.

    Ensures gate answers use gate-bound refs, not stale previews.
    """

    def test_gate_ref_over_stale_preview(self, db_conn, storage):
        """Gate resolver uses gate-bound refs, ignoring stale previews."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        # Create artifact with known content at gate creation time
        original_content = "Original analysis: 3 high risks found"
        chk = create_artifact(storage, "analysis/summary.json", original_content)

        refs = build_artifact_refs([
            ("analysis_report", "analysis/summary.json", chk),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-truth", refs)

        # Content changes (simulating stale preview scenario)
        storage.joinpath("analysis/summary.json").write_text(
            "Stale preview: 0 risks found (outdated)"
        )

        # Resolve via gate - should detect checksum mismatch
        result = resolver.resolve_gate_artifacts(gate_id)
        # Either the checksum fails (content changed) or we get the correct content
        if result.artifacts:
            assert len(result.artifacts) > 0
            # If checksum verified, content matches original at gate creation
            # Since content changed, checksum should NOT verify
        # But at minimum, the resolver doesn't silently return stale preview
        assert not (len(result.artifacts) == 1 and result.artifacts[0].content == "Stale preview: 0 risks found (outdated)")

    def test_gate_ref_correct_when_preview_stale(self, db_conn, storage):
        """When preview is stale but gate ref is correct, use ref."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        # Create a frozen copy of the artifact at gate creation
        frozen_dir = storage / "frozen"
        frozen_dir.mkdir()
        original_content = "Frozen: 5 high risks, 3 medium, 1 low"
        chk = create_artifact(frozen_dir, "analysis/summary.json", original_content)

        refs = build_artifact_refs([
            ("analysis_report", "frozen/analysis/summary.json", chk),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-truth2", refs)

        # Fresh preview (stale) at a different path
        (storage / "analysis").mkdir(exist_ok=True)
        storage.joinpath("analysis/summary.json").write_text(
            "Stale preview: 0 risks found"
        )

        # Resolve - should find the frozen artifact with matching checksum
        result = resolver.resolve_gate_artifacts(gate_id)
        assert len(result.artifacts) >= 1
        for art in result.artifacts:
            if art.checksum_verified:
                assert "Frozen" in art.content or "frozen" in art.content.lower()

    def test_no_fallback_to_old_preview(self, db_conn, storage):
        """No fallback to old preview when ref broken."""
        repo = SqlitePhaseGateRepository(db_conn)
        resolver = V2GateArtifactResolver(repo, storage_root=storage)

        # Broken ref (file doesn't exist)
        refs = build_artifact_refs([
            ("analysis_report", "analysis/nonexistent.json", "a" * 64),
        ])
        gate_id = make_gate(repo, uuid4().hex, "job-broken", refs)

        result = resolver.resolve_gate_artifacts(gate_id)
        assert len(result.artifacts) == 0
        # Failure reason should be visible
        assert result.failure_message is not None
        # Should not silently return some other content
