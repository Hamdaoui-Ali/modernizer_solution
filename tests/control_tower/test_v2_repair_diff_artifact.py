"""F5-T6: Final reviewed repair diff artifact tests — checksum, files, review chain."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from migration_factory.control_tower.application.v2_assistant_model_client import (
    V2AssistantModelResult,
)
from migration_factory.control_tower.application.v2_model_role_router import V2ModelRole
from migration_factory.control_tower.domain.checksums import sha256_canonical_json
from migration_factory.orchestrator.repair_review_chain import (
    RepairArtifactPhase,
    _build_final_reviewed_repair_artifact,
    _compute_final_repair_artifact_checksum,
    produce_repair_review_chain,
)
from migration_factory.repair_loop.failure_evidence import (
    FailureEvidence,
    FailureSource,
    build_failure_evidence,
)
from migration_factory.repair_loop.repair_context import (
    RepairContextPack,
    build_repair_context_pack,
)


# ── Helpers ────────────────────────────────────────────────────────


class _FakeClient:
    def __init__(self, reviewer_decision="accept"):
        self._call_count = 0
        self._reviewer_decision = reviewer_decision

    def answer_with_role(self, *, role, prompt, fallback, **kwargs):
        self._call_count += 1
        if role.value == "proposer" or role == V2ModelRole.PROPOSER:
            content = json.dumps({
                "root_cause": "test failure",
                "fix_strategy": "fix test",
                "changed_files": ["Test.java"],
                "proposed_diff": (
                    "--- a/Test.java\n+++ b/Test.java\n@@ -1,1 +1,1 @@\n-old\n+new"
                ),
                "deterministic_rule_id": "TEST_FIX",
                "risk": "LOW",
                "confidence": 0.9,
                "rationale": "fix test",
            })
            return V2AssistantModelResult(
                content=content,
                source="fake",
                model_status="ok",
                provider="fake",
                role="proposer",
                success=True,
                redacted_summary="",
                failure_reason="",
            )
        else:
            return V2AssistantModelResult(
                content=json.dumps({
                    "decision": self._reviewer_decision,
                    "notes": ["ok"],
                    "confidence": 0.9,
                    "risks": [],
                    "policy_concerns": [],
                }),
                source="fake",
                model_status="ok",
                provider="fake",
                role="reviewer",
                success=True,
                redacted_summary="",
                failure_reason="",
            )


def _make_evidence():
    return build_failure_evidence(
        failure_source=FailureSource.BUILD,
        job_id="job-1",
        stage_index=0,
        command_id="cmd-1",
        failure_summary="compilation error in Test.java",
        changed_files=("Test.java",),
    )


def _make_context_pack(evidence):
    return build_repair_context_pack(
        failure_evidence=evidence,
        job_id="job-1",
        stage_index=0,
        command_id="cmd-1",
    )


def _make_primary_output():
    return {
        "root_cause": "missing semicolon",
        "fix_strategy": "add semicolon",
        "changed_files": ["Test.java"],
        "proposed_diff": "--- a/Test.java\n+++ b/Test.java\n@@ -1,1 +1,1 @@\n-old\n+new",
        "deterministic_rule_id": "SYNTAX_FIX",
        "risk": "LOW",
        "confidence": 0.95,
        "rationale": "simple syntax fix",
    }


def _make_reviewer_output():
    return {
        "decision": "accept",
        "notes": ["safe change"],
        "confidence": 0.92,
        "risks": [],
        "policy_concerns": [],
        "reviewed_context_checksum": "ctx_cs",
        "reviewed_primary_output_checksum": "pri_cs",
        "reviewed_diff_checksum": "diff_cs",
    }


# ── Test 1 ─────────────────────────────────────────────────────────


def test_build_final_artifact_all_required_fields():
    """_build_final_reviewed_repair_artifact creates artifact with all required fields."""
    evidence = _make_evidence()
    context_pack = _make_context_pack(evidence)
    primary = _make_primary_output()
    reviewer = _make_reviewer_output()
    artifact = _build_final_reviewed_repair_artifact(
        job_id="job-1",
        stage_index=0,
        failure_evidence=evidence,
        context_pack=context_pack,
        primary_output=primary,
        primary_checksum="pri_cs",
        reviewer_output=reviewer,
        reviewer_checksum="rev_cs",
        deterministic_checksum="det_cs",
    )
    required_fields = [
        "schema_version", "proposal_id", "job_id", "stage_index",
        "failure_source", "failure_summary", "deterministic_artifact_checksum",
        "context_pack_checksum", "primary_output_checksum",
        "reviewer_output_checksum", "proposed_diff_checksum",
        "changed_files", "base_repo_state_checksum", "root_cause",
        "fix_strategy", "risk", "confidence", "reviewer_decision",
        "reviewer_notes", "policy_validation_checksum",
        "artifact_checksum", "created_at",
    ]
    for field in required_fields:
        assert field in artifact, f"missing field {field!r} in final artifact"


# ── Test 2 ─────────────────────────────────────────────────────────


def test_final_artifact_checksum_deterministic():
    """Final artifact checksum is deterministic — same inputs produce same checksum."""
    evidence = _make_evidence()
    context_pack = _make_context_pack(evidence)
    primary = _make_primary_output()
    reviewer = _make_reviewer_output()

    def build():
        return _build_final_reviewed_repair_artifact(
            job_id="job-1",
            stage_index=0,
            failure_evidence=evidence,
            context_pack=context_pack,
            primary_output=primary,
            primary_checksum="pri_cs",
            reviewer_output=reviewer,
            reviewer_checksum="rev_cs",
            deterministic_checksum="det_cs",
        )

    a1 = build()
    a2 = build()
    cs1 = _compute_final_repair_artifact_checksum(a1)
    cs2 = _compute_final_repair_artifact_checksum(a2)
    assert cs1 == cs2


# ── Test 3 ─────────────────────────────────────────────────────────


def test_final_artifact_checksum_changes_when_diff_changes():
    """Final artifact checksum changes when the proposed diff changes."""
    evidence = _make_evidence()
    context_pack = _make_context_pack(evidence)
    reviewer = _make_reviewer_output()

    primary1 = dict(_make_primary_output(), proposed_diff="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new")
    primary2 = dict(_make_primary_output(), proposed_diff="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+different")

    a1 = _build_final_reviewed_repair_artifact(
        job_id="job-1", stage_index=0,
        failure_evidence=evidence, context_pack=context_pack,
        primary_output=primary1, primary_checksum="pri1",
        reviewer_output=reviewer, reviewer_checksum="rev_cs",
        deterministic_checksum="det_cs",
    )
    a2 = _build_final_reviewed_repair_artifact(
        job_id="job-1", stage_index=0,
        failure_evidence=evidence, context_pack=context_pack,
        primary_output=primary2, primary_checksum="pri2",
        reviewer_output=reviewer, reviewer_checksum="rev_cs",
        deterministic_checksum="det_cs",
    )
    cs1 = _compute_final_repair_artifact_checksum(a1)
    cs2 = _compute_final_repair_artifact_checksum(a2)
    assert cs1 != cs2


# ── Test 4 ─────────────────────────────────────────────────────────


def test_diff_path_written_alongside_artifact(tmp_path):
    """diff_path is written alongside artifact when produce_repair_review_chain runs."""
    output_dir = tmp_path / "repair_out"
    evidence = _make_evidence()
    context_pack = _make_context_pack(evidence)
    client = _FakeClient()

    result = produce_repair_review_chain(
        failure_evidence=evidence,
        context_pack=context_pack,
        output_dir=output_dir,
        model_client=client,
    )

    diff_file = output_dir / "final_reviewed_repair.diff"
    assert diff_file.exists()
    assert diff_file.read_text() != ""


# ── Test 5 ─────────────────────────────────────────────────────────


def test_review_chain_json_includes_all_checksums(tmp_path):
    """review_chain.json includes all checksums."""
    output_dir = tmp_path / "repair_out2"
    evidence = _make_evidence()
    context_pack = _make_context_pack(evidence)
    client = _FakeClient()

    produce_repair_review_chain(
        failure_evidence=evidence,
        context_pack=context_pack,
        output_dir=output_dir,
        model_client=client,
    )

    chain_path = output_dir / "review_chain.json"
    assert chain_path.exists()
    chain = json.loads(chain_path.read_text())
    expected_keys = [
        "deterministic_artifact_checksum",
        "context_pack_checksum",
        "primary_output_checksum",
        "reviewer_output_checksum",
        "proposed_diff_checksum",
        "final_artifact_checksum",
    ]
    for key in expected_keys:
        assert key in chain, f"missing checksum key {key!r} in review_chain.json"
        assert chain[key], f"checksum {key!r} should not be empty"


# ── Test 6 ─────────────────────────────────────────────────────────


def test_artifact_phase_repair_supported():
    """Artifact revision kind "repair" is supported in RepairArtifactPhase."""
    assert RepairArtifactPhase.REPAIR == "repair"


# ── Test 7 ─────────────────────────────────────────────────────────


def test_produce_repair_review_chain_creates_all_output_files(tmp_path):
    """Running produce_repair_review_chain successfully creates all output files."""
    output_dir = tmp_path / "full_chain"
    evidence = _make_evidence()
    context_pack = _make_context_pack(evidence)
    client = _FakeClient()

    result = produce_repair_review_chain(
        failure_evidence=evidence,
        context_pack=context_pack,
        output_dir=output_dir,
        model_client=client,
    )

    assert "artifact_refs" in result
    assert "review_chain" in result

    refs = result["artifact_refs"]
    expected_files = [
        "deterministic_artifact",
        "primary_llm_output",
        "reviewer_llm_output",
        "final_reviewed_artifact",
        "final_reviewed_diff",
        "review_chain_metadata",
    ]
    for key in expected_files:
        assert key in refs, f"missing ref key {key!r}"
        path = Path(refs[key])
        assert path.exists(), f"file {path} does not exist"


# ── Test 8 ─────────────────────────────────────────────────────────


def test_final_diff_identical_to_proposed_on_accept(tmp_path):
    """Final reviewed diff is identical to primary proposed diff when reviewer accepts."""
    output_dir = tmp_path / "accept_chain"
    evidence = _make_evidence()
    context_pack = _make_context_pack(evidence)
    client = _FakeClient(reviewer_decision="accept")

    result = produce_repair_review_chain(
        failure_evidence=evidence,
        context_pack=context_pack,
        output_dir=output_dir,
        model_client=client,
    )

    primary_path = Path(result["artifact_refs"]["primary_llm_output"])
    primary = json.loads(primary_path.read_text())
    diff_path = Path(result["artifact_refs"]["final_reviewed_diff"])
    diff_content = diff_path.read_text().strip()
    assert diff_content == primary.get("proposed_diff", "").strip()


# ── Test 9 ─────────────────────────────────────────────────────────


def test_review_chain_includes_deterministic_artifact_checksum(tmp_path):
    """Review chain includes deterministic_artifact_checksum."""
    output_dir = tmp_path / "det_checksum"
    evidence = _make_evidence()
    context_pack = _make_context_pack(evidence)

    result = produce_repair_review_chain(
        failure_evidence=evidence,
        context_pack=context_pack,
        output_dir=output_dir,
        model_client=_FakeClient(),
    )

    chain = result["review_chain"]
    assert "deterministic_artifact_checksum" in chain
    assert chain["deterministic_artifact_checksum"]


# ── Test 10 ────────────────────────────────────────────────────────


def test_review_chain_includes_context_pack_checksum(tmp_path):
    """Review chain includes context_pack_checksum."""
    output_dir = tmp_path / "ctx_checksum"
    evidence = _make_evidence()
    context_pack = _make_context_pack(evidence)

    result = produce_repair_review_chain(
        failure_evidence=evidence,
        context_pack=context_pack,
        output_dir=output_dir,
        model_client=_FakeClient(),
    )

    chain = result["review_chain"]
    assert "context_pack_checksum" in chain
    assert chain["context_pack_checksum"]
