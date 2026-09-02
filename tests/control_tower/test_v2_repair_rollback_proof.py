"""Tests for F5-T13: Rollback and Proof — deterministic checksums and artifact proof."""

from __future__ import annotations

import pytest

from migration_factory.control_tower.domain.checksums import (
    sha256_canonical_json,
)
from migration_factory.orchestrator.repair_review_chain import (
    _compute_final_repair_artifact_checksum,
)
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
    compute_base_repo_state_checksum,
)


# ── T13-1: compute_final_repair_artifact_checksum is deterministic ──────

def test_compute_final_repair_artifact_checksum_deterministic() -> None:
    payload = {"root_cause": "missing dep", "fix_strategy": "add dependency"}
    a = _compute_final_repair_artifact_checksum(payload)
    b = _compute_final_repair_artifact_checksum(dict(payload))

    assert a == b
    assert a != ""


# ── T13-2: compute_final_repair_artifact_checksum includes all stable fields ──

def test_compute_final_repair_artifact_checksum_includes_all_stable_fields() -> None:
    base = {"root_cause": "missing dep", "fix_strategy": "add dependency"}
    a = _compute_final_repair_artifact_checksum(base)
    b = _compute_final_repair_artifact_checksum({**base, "extra_stable": "value"})

    assert a != b


# ── T13-3: compute_final_repair_artifact_checksum excludes created_at ───

def test_compute_final_repair_artifact_checksum_excludes_created_at() -> None:
    base = {"root_cause": "missing dep"}
    a = _compute_final_repair_artifact_checksum({**base, "created_at": "2020-01-01T00:00:00Z"})
    b = _compute_final_repair_artifact_checksum({**base, "created_at": "2026-06-27T12:00:00Z"})

    assert a == b


# ── T13-4: Failure evidence artifact_checksum includes content_checksum ─

def test_failure_evidence_artifact_checksum_includes_content_checksum() -> None:
    evidence = build_failure_evidence(
        failure_source=FailureSource.BUILD,
        command_id="cmd-1",
        failure_summary="error",
    )

    assert evidence.artifact_checksum != ""
    assert evidence.artifact_checksum != evidence.content_checksum


# ── T13-5: Artifact checksum changes when created_at differs (same content) ──

def test_failure_evidence_artifact_checksum_changes_on_created_at() -> None:
    e1 = build_failure_evidence(
        failure_source=FailureSource.BUILD,
        command_id="cmd-1",
        failure_summary="same error",
    )
    e2 = build_failure_evidence(
        failure_source=FailureSource.BUILD,
        command_id="cmd-1",
        failure_summary="same error",
    )

    assert e1.content_checksum == e2.content_checksum
    assert e1.artifact_checksum != e2.artifact_checksum


# ── T13-6: compute_failure_content_checksum is stable for same inputs ───

def test_compute_failure_content_checksum_stable_for_same_inputs() -> None:
    e1 = build_failure_evidence(
        failure_source=FailureSource.BUILD,
        stage_index=1,
        job_id="job-1",
        command_id="cmd-1",
        failure_summary="Compilation error",
        compiler_errors=(
            NormalizedCompilerError(
                message="cannot find symbol",
                file_path="App.java",
                line=10,
                column=5,
                severity="error",
            ),
        ),
    )
    e2 = build_failure_evidence(
        failure_source=FailureSource.BUILD,
        stage_index=1,
        job_id="job-1",
        command_id="cmd-1",
        failure_summary="Compilation error",
        compiler_errors=(
            NormalizedCompilerError(
                message="cannot find symbol",
                file_path="App.java",
                line=10,
                column=5,
                severity="error",
            ),
        ),
    )

    assert e1.content_checksum == e2.content_checksum


# ── T13-7: compute_failure_content_checksum changes on different failure_source ──

def test_compute_failure_content_checksum_changes_on_different_failure_source() -> None:
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


# ── T13-8: compute_failure_content_checksum changes on different compiler_errors ──

def test_compute_failure_content_checksum_changes_on_different_compiler_errors() -> None:
    e1 = build_failure_evidence(
        failure_source=FailureSource.BUILD,
        command_id="cmd-1",
        failure_summary="Compilation error",
        compiler_errors=(
            NormalizedCompilerError(
                message="cannot find symbol X",
                file_path="App.java",
                line=10,
                column=5,
                severity="error",
            ),
        ),
    )
    e2 = build_failure_evidence(
        failure_source=FailureSource.BUILD,
        command_id="cmd-1",
        failure_summary="Compilation error",
        compiler_errors=(
            NormalizedCompilerError(
                message="cannot find symbol Y",
                file_path="App.java",
                line=10,
                column=5,
                severity="error",
            ),
        ),
    )

    assert e1.content_checksum != e2.content_checksum


# ── T13-9: compute_base_repo_state_checksum includes all required fields ─

def test_compute_base_repo_state_checksum_includes_all_required_fields() -> None:
    a = compute_base_repo_state_checksum(
        changed_files=("pom.xml",),
        file_checksums={"pom.xml": "abc"},
        source_profile="java8",
        target_profile="java17",
        accepted_artifact_checksums=("artifact-check-1",),
    )
    b = compute_base_repo_state_checksum(
        changed_files=("pom.xml",),
        file_checksums={"pom.xml": "abc"},
        source_profile="java8",
        target_profile="java17",
        accepted_artifact_checksums=("artifact-check-1",),
    )

    assert a == b

    c = compute_base_repo_state_checksum(
        changed_files=("pom.xml", "App.java"),
        file_checksums={"pom.xml": "abc"},
        source_profile="java8",
        target_profile="java17",
        accepted_artifact_checksums=("artifact-check-1",),
    )
    assert a != c


# ── T13-10: Final repair artifact with accept decision has correct checksum ──

def test_final_repair_artifact_with_accept_decision_has_correct_checksum() -> None:
    accept_payload = {
        "root_cause": "missing dependency",
        "fix_strategy": "add h2 dependency",
        "reviewer_decision": "accept",
    }
    reject_payload = {
        "root_cause": "missing dependency",
        "fix_strategy": "add h2 dependency",
        "reviewer_decision": "reject",
    }

    accept_checksum = _compute_final_repair_artifact_checksum(accept_payload)
    reject_checksum = _compute_final_repair_artifact_checksum(reject_payload)

    assert accept_checksum != reject_checksum
    assert accept_checksum != ""
    assert reject_checksum != ""
