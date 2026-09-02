"""Focused tests for F15 job007 — gate checksum contract."""

from __future__ import annotations

import pytest

from migration_factory.control_tower.domain.gate_checksum import (
    GateChecksumMismatchError,
    gate_checksum,
)


def test_gate_checksum_deterministic() -> None:
    a = gate_checksum(
        gate_id="gate-1",
        job_id="job-1",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=["artifact-1", "artifact-2"],
    )
    b = gate_checksum(
        gate_id="gate-1",
        job_id="job-1",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=["artifact-2", "artifact-1"],  # different order
    )
    assert a == b  # refs are sorted


def test_gate_checksum_changes_with_artifact_refs() -> None:
    a = gate_checksum(
        gate_id="gate-1",
        job_id="job-1",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=["artifact-1"],
    )
    b = gate_checksum(
        gate_id="gate-1",
        job_id="job-1",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=["artifact-1", "artifact-2"],  # extra ref
    )
    assert a != b


def test_gate_checksum_changes_with_phase() -> None:
    a = gate_checksum(
        gate_id="gate-1",
        job_id="job-1",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=["artifact-1"],
    )
    b = gate_checksum(
        gate_id="gate-1",
        job_id="job-1",
        gate_phase="planning_review",
        stage_index=1,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=["artifact-1"],
    )
    assert a != b


def test_gate_checksum_changes_with_stage() -> None:
    a = gate_checksum(
        gate_id="gate-1",
        job_id="job-1",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=["artifact-1"],
    )
    b = gate_checksum(
        gate_id="gate-1",
        job_id="job-1",
        gate_phase="analysis_review",
        stage_index=2,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=["artifact-1"],
    )
    assert a != b


def test_gate_checksum_changes_with_source_checksum() -> None:
    a = gate_checksum(
        gate_id="gate-1",
        job_id="job-1",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=["artifact-1"],
    )
    b = gate_checksum(
        gate_id="gate-1",
        job_id="job-1",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:xyz",
        source_artifact_refs=["artifact-1"],
    )
    assert a != b


def test_gate_checksum_changes_with_gate_id() -> None:
    a = gate_checksum(
        gate_id="gate-1",
        job_id="job-1",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=["artifact-1"],
    )
    b = gate_checksum(
        gate_id="gate-2",
        job_id="job-1",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=["artifact-1"],
    )
    assert a != b


def test_stale_checksum_mismatch_error() -> None:
    err = GateChecksumMismatchError(
        expected_checksum="sha256:deadbeef1234",
        actual_checksum="sha256:cafebabe5678",
        gate_id="gate-abc",
    )
    assert "stale" in str(err).lower() or "mismatch" in str(err).lower()
    assert "gate-abc" in str(err)
    assert "deadbeef" in str(err)


def test_gate_checksum_with_extra() -> None:
    a = gate_checksum(
        gate_id="gate-1",
        job_id="job-1",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=["artifact-1"],
        extra={"key": "value"},
    )
    b = gate_checksum(
        gate_id="gate-1",
        job_id="job-1",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=["artifact-1"],
        extra={"key": "value"},
    )
    assert a == b

    c = gate_checksum(
        gate_id="gate-1",
        job_id="job-1",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=["artifact-1"],
        extra={"key": "different"},
    )
    assert a != c


def test_empty_artifact_refs() -> None:
    chk = gate_checksum(
        gate_id="gate-1",
        job_id="job-1",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=[],
    )
    assert isinstance(chk, str)
    assert len(chk) == 64  # SHA-256 hex


def test_checksum_is_hex_sha256() -> None:
    chk = gate_checksum(
        gate_id="gate-1",
        job_id="job-1",
        gate_phase="analysis_review",
        stage_index=1,
        source_artifact_checksum="sha256:abc",
        source_artifact_refs=["a1"],
    )
    assert len(chk) == 64
    assert all(c in "0123456789abcdef" for c in chk)
