from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from migration_factory.control_tower.application.v2_assistant_model_client import (
    V2AssistantModelResult,
)
from migration_factory.control_tower.application.v2_model_role_router import V2ModelRole
from migration_factory.control_tower.application.v2_orchestrator_runner import (
    V2OrchestratorRunner,
)
from migration_factory.control_tower.application.v2_review_chain_contracts import (
    validate_runtime_review_chain_result,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
    SqliteUnitOfWork,
)
from migration_factory.orchestrator.review_chain import (
    ReviewChainProductionError,
    produce_phase_review_chain,
)


class _FakeModelClient:
    def __init__(self, *, reviewer_decision: str = "accept", checksum_mismatch: bool = False) -> None:
        self.reviewer_decision = reviewer_decision
        self.checksum_mismatch = checksum_mismatch
        self.calls: list[V2ModelRole] = []

    def answer_with_role(self, *, role: V2ModelRole, prompt: str, fallback: str, **_: Any) -> V2AssistantModelResult:
        self.calls.append(role)
        if role == V2ModelRole.PROPOSER:
            content = json.dumps(
                {
                    "reasoning": "Primary reasoning grounded in deterministic artifact evidence.",
                    "risks": ["Review dependent on supplied deterministic artifacts."],
                    "confidence": 0.82,
                    "recommended_next_step": "Open the human review checkpoint.",
                    "draft_markdown": "# Draft\nReviewed checkpoint draft.",
                },
                sort_keys=True,
            )
        else:
            content = json.dumps(
                {
                    "decision": self.reviewer_decision,
                    "notes": ["Reviewer checked deterministic and primary checksums."],
                    "confidence": 0.91,
                    "risks": [],
                    "policy_concerns": [],
                    "reviewed_artifact_checksum": "mismatch" if self.checksum_mismatch else "",
                    "reviewed_primary_output_checksum": "",
                    "review_dimensions": {"checksum_match": not self.checksum_mismatch},
                },
                sort_keys=True,
            )
        return V2AssistantModelResult(
            content=content,
            source="fake",
            model_status="live_ok",
            provider="fake",
            role=role.value,
            success=True,
            redacted_summary="ok",
            failure_reason="",
        )


class _FailingModelClient:
    def answer_with_role(self, *, role: V2ModelRole, prompt: str, fallback: str, **_: Any) -> V2AssistantModelResult:
        return V2AssistantModelResult(
            content=fallback,
            source="deterministic",
            model_status="fallback",
            provider="deterministic",
            role=role.value,
            success=False,
            redacted_summary="missing model",
            failure_reason="missing_deployment",
        )


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "review-chain.sqlite3", isolation_level=None)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    return conn


def _state(tmp_path: Path, *, phase: str) -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "run_id": f"run-{phase}",
        "modernized_app_path": str(tmp_path / "modernized"),
        "source_profile": "springboot-2.7-java11",
        "target_profile": "springboot-3.5-java17",
    }


def _artifact_refs(tmp_path: Path, *, phase: str) -> dict[str, str]:
    phase_dir = tmp_path / "modernized" / ".migration" / "runs" / f"run-{phase}" / phase
    phase_dir.mkdir(parents=True, exist_ok=True)
    deterministic = phase_dir / ("analysis_report.json" if phase == "analysis" else "migration_plan.yaml")
    deterministic.write_text('{"status":"PASS"}', encoding="utf-8")
    return {
        "analysis_report" if phase == "analysis" else "migration_plan.yaml": str(deterministic),
    }


def _produce(tmp_path: Path, *, phase: str, client: Any | None = None) -> dict[str, Any]:
    return produce_phase_review_chain(
        _state(tmp_path, phase=phase),
        phase=phase,
        stage_index=1 if phase == "analysis" else 2,
        artifact_refs=_artifact_refs(tmp_path, phase=phase),
        deterministic_facts={"status": "PASS", "phase": phase},
        warnings=[],
        model_client=client or _FakeModelClient(),
    )


def test_stage1_analysis_produces_valid_review_chain(tmp_path: Path) -> None:
    updates = _produce(tmp_path, phase="analysis")

    assert {"deterministic_artifact", "primary_llm_output", "reviewer_llm_output", "final_reviewed_markdown", "review_chain_metadata"} <= set(updates["artifact_refs"])
    failures = validate_runtime_review_chain_result(
        {"job_id": "job-1", **updates},
        phase="analysis",
        stage_index=1,
        expected_job_id="job-1",
    )
    assert failures == []

    conn = _conn(tmp_path)
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=None,
        cwd=tmp_path,
    )
    runner._handle_reviewed_phase_completed(
        job_id="job-1",
        stage_index=1,
        command_id="cmd-1",
        result={"job_id": "job-1", **updates},
        phase="analysis",
        gate_phase="analysis_review",
        required_event_type="analysis_review_required",
    )

    gate = SqliteUnitOfWork(conn).phase_gates.find_open("job-1", "analysis_review", 1)
    assert gate is not None
    assert "final_reviewed_markdown" in gate.source_artifact_refs_json


def test_stage2_planning_produces_valid_review_chain(tmp_path: Path) -> None:
    updates = _produce(tmp_path, phase="planning")

    assert {"deterministic_artifact", "primary_llm_output", "reviewer_llm_output", "final_reviewed_markdown", "review_chain_metadata"} <= set(updates["artifact_refs"])
    failures = validate_runtime_review_chain_result(
        {"job_id": "job-1", **updates},
        phase="planning",
        stage_index=2,
        expected_job_id="job-1",
    )
    assert failures == []

    conn = _conn(tmp_path)
    runner = V2OrchestratorRunner(
        unit_of_work_factory=lambda: SqliteUnitOfWork(conn),
        popen_factory=None,
        cwd=tmp_path,
    )
    runner._handle_reviewed_phase_completed(
        job_id="job-1",
        stage_index=2,
        command_id="cmd-1",
        result={"job_id": "job-1", **updates},
        phase="planning",
        gate_phase="planning_review",
        required_event_type="planning_review_required",
    )

    gate = SqliteUnitOfWork(conn).phase_gates.find_open("job-1", "planning_review", 2)
    assert gate is not None
    assert "final_reviewed_markdown" in gate.source_artifact_refs_json


def test_stage1_analysis_review_chain_missing_or_rejected_fails_closed(tmp_path: Path) -> None:
    try:
        _produce(tmp_path, phase="analysis", client=_FakeModelClient(reviewer_decision="reject"))
    except ReviewChainProductionError as exc:
        assert "reviewer rejected" in str(exc)
    else:
        raise AssertionError("rejected reviewer output must fail closed")

    try:
        _produce(tmp_path, phase="analysis", client=_FailingModelClient())
    except ReviewChainProductionError as exc:
        assert "model failed closed" in str(exc)
    else:
        raise AssertionError("missing model output must fail closed")


def test_stage2_planning_review_chain_missing_or_rejected_fails_closed(tmp_path: Path) -> None:
    try:
        _produce(tmp_path, phase="planning", client=_FakeModelClient(reviewer_decision="request_revision"))
    except ReviewChainProductionError as exc:
        assert "reviewer requested revision" in str(exc)
    else:
        raise AssertionError("revision reviewer output must fail closed")

    try:
        _produce(tmp_path, phase="planning", client=_FakeModelClient(checksum_mismatch=True))
    except ReviewChainProductionError as exc:
        assert "checksum mismatch" in str(exc)
    else:
        raise AssertionError("checksum mismatch must fail closed")


def test_review_chain_rejects_primary_output_as_final_reviewed_markdown(tmp_path: Path) -> None:
    updates = _produce(tmp_path, phase="analysis")
    result = {"job_id": "job-1", **updates}
    result["artifact_refs"]["final_reviewed_markdown"] = result["artifact_refs"]["primary_llm_output"]
    result["review_chain"]["final_markdown_ref"] = result["review_chain"]["primary_output_ref"]

    failures = validate_runtime_review_chain_result(
        result,
        phase="analysis",
        stage_index=1,
        expected_job_id="job-1",
    )

    assert any("primary output" in failure for failure in failures)


def test_review_chain_public_projection_redacts_execution_internals(tmp_path: Path) -> None:
    updates = _produce(tmp_path, phase="analysis")
    public_blob = json.dumps(
        {
            "artifact_refs": updates["artifact_refs"],
            "review_chain": updates["review_chain"],
        },
        sort_keys=True,
    ).lower()

    for forbidden in (
        "sandbox_path",
        "argv",
        "raw_command",
        "endpoint",
        "deployment",
        "env_ref",
        "filesystem_target",
        "user_supplied_file_path",
    ):
        assert forbidden not in public_blob
