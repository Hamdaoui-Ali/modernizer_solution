from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Sequence

import yaml

from migration_factory.approval import (
    ApprovalArtifactError,
    build_approved_plan_lock,
    check_approval_decision,
    check_approved_plan_lock,
    write_approval_decision,
    write_approved_plan_lock,
)
from migration_factory.contracts import APPROVAL_DECISION_VALUES
from migration_factory.contracts.schema_validation import validate_against_schema


REQUIRED_PHASE_1_ARTIFACTS = (
    "analysis/analysis_report.json",
    "analysis/dependency_graph.json",
    "analysis/test_inventory.json",
    "analysis/analysis_summary.md",
    "planning/migration_plan.yaml",
    "planning/approval_request.json",
    "assessment/assessment_report.json",
    "analysis/read_only_verification.json",
)

SCHEMA_BACKED_ARTIFACTS = {
    "analysis/analysis_report.json": "analysis_report.schema.json",
    "planning/migration_plan.yaml": "migration_plan.schema.json",
    "planning/approval_request.json": "approval_request.schema.json",
    "assessment/assessment_report.json": "assessment_report.schema.json",
    "analysis/read_only_verification.json": "read_only_verification.schema.json",
}

ASSESSMENT_EXECUTION_CLAIMS = (
    "transformation_executed",
    "openrewrite_apply_executed",
    "migrated_build_executed",
    "migrated_tests_executed",
    "final_migration_executed",
)


class ApprovalCliError(ValueError):
    """Raised when a run cannot be safely approved."""


@dataclass(frozen=True)
class ApprovalRunResult:
    approval_decision: Path
    approved_plan_lock: Path | None


class _ApprovalArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ApprovalCliError(message)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        parser = _build_parser()
        args = parser.parse_args(argv)
        result = record_approval_decision_for_run(
            run_dir=Path(args.run_dir),
            run_id=args.run_id,
            decided_by=args.approved_by,
            decision=args.decision,
            comments=args.comments,
            source="approve_run_cli",
            require_approved=True,
        )
    except (ApprovalCliError, ApprovalArtifactError) as exc:
        print("APPROVAL_FAILED")
        print(f"ERROR: {exc}")
        return 1

    print("APPROVAL_RECORDED")
    print(f"approval_decision: {result.approval_decision}")
    print(f"approved_plan_lock: {result.approved_plan_lock}")
    print("APPROVED_FOR_PHASE_2")
    return 0


def approve_run(
    *,
    run_dir: Path,
    run_id: str,
    approved_by: str,
    decision: str,
    comments: str,
) -> tuple[Path, Path]:
    result = record_approval_decision_for_run(
        run_dir=run_dir,
        run_id=run_id,
        decided_by=approved_by,
        decision=decision,
        comments=comments,
        source="approve_run_cli",
        require_approved=True,
    )
    if result.approved_plan_lock is None:
        raise ApprovalCliError("approved decision did not create approved_plan_lock.json")
    return result.approval_decision, result.approved_plan_lock


def record_approval_decision_for_run(
    *,
    run_dir: Path,
    run_id: str,
    decided_by: str,
    decision: str,
    comments: str = "",
    source: str = "orchestrator_resume",
    require_approved: bool = False,
) -> ApprovalRunResult:
    run_dir = run_dir.resolve()
    _validate_request(
        run_id=run_id,
        approved_by=decided_by,
        decision=decision,
        require_approved=require_approved,
    )
    _validate_phase_1_artifacts(run_dir=run_dir, run_id=run_id)

    plan_lock: Path | None = None
    plan_lock_ref: str | None = None
    if decision == "approved":
        build_approved_plan_lock(run_dir, run_id)
        plan_lock = write_approved_plan_lock(run_dir=run_dir, run_id=run_id)
        plan_lock_ref = "approved_plan_lock.json"

    approval_decision = write_approval_decision(
        run_dir,
        run_id,
        decision,
        decided_by=decided_by,
        comments=comments,
        plan_lock_ref=plan_lock_ref,
        source=source,
        artifact_refs={
            "approval_request": "../planning/approval_request.json",
            "assessment_report": "../assessment/assessment_report.json",
        },
    )
    decision_errors = check_approval_decision(run_dir, expected_run_id=run_id)
    if decision_errors:
        raise ApprovalCliError("; ".join(decision_errors))

    if decision == "approved":
        lock_errors = check_approved_plan_lock(run_dir, expected_run_id=run_id)
        if lock_errors:
            raise ApprovalCliError("; ".join(lock_errors))

    return ApprovalRunResult(
        approval_decision=approval_decision,
        approved_plan_lock=plan_lock,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = _ApprovalArgumentParser(description="Record human approval for an existing Phase 1 run.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--decision", required=True)
    parser.add_argument("--comments", default="")
    return parser


def _validate_request(
    *,
    run_id: str,
    approved_by: str,
    decision: str,
    require_approved: bool = False,
) -> None:
    if not run_id:
        raise ApprovalCliError("--run-id is required")
    if not approved_by:
        raise ApprovalCliError("--approved-by is required")
    if decision not in APPROVAL_DECISION_VALUES:
        raise ApprovalCliError(f"Unsupported approval decision: {decision}")
    if require_approved and decision != "approved":
        raise ApprovalCliError(f"approval CLI only records approved decisions, got {decision!r}")


def _validate_phase_1_artifacts(*, run_dir: Path, run_id: str) -> dict[str, Any]:
    errors: list[str] = []
    for rel_path in REQUIRED_PHASE_1_ARTIFACTS:
        if not (run_dir / rel_path).is_file():
            errors.append(f"Missing required artifact: {rel_path}")
    if errors:
        raise ApprovalCliError("; ".join(errors))

    artifacts: dict[str, Any] = {}
    for rel_path, schema_name in SCHEMA_BACKED_ARTIFACTS.items():
        payload = _load_artifact(run_dir / rel_path)
        artifacts[rel_path] = payload
        errors.extend(
            f"Invalid artifact schema for {rel_path}: {error}"
            for error in validate_against_schema(payload, schema_name)
        )
        if isinstance(payload, dict) and payload.get("run_id") != run_id:
            errors.append(f"{rel_path} run_id mismatch")

    read_only = artifacts.get("analysis/read_only_verification.json")
    if isinstance(read_only, dict) and read_only.get("source_modified") is not False:
        errors.append("read_only_verification.json source_modified must be false")

    assessment = artifacts.get("assessment/assessment_report.json")
    if isinstance(assessment, dict):
        readiness = assessment.get("approval_readiness")
        readiness_status = readiness.get("status") if isinstance(readiness, dict) else readiness
        if readiness_status != "READY_FOR_REVIEW":
            errors.append("assessment_report.json approval_readiness must be READY_FOR_REVIEW")
        blockers = assessment.get("blockers")
        if blockers:
            errors.append("assessment_report.json blockers must be empty")
        claims = assessment.get("execution_claims")
        if isinstance(claims, dict):
            for claim in ASSESSMENT_EXECUTION_CLAIMS:
                if claims.get(claim) is True:
                    errors.append(f"assessment_report.json execution claim {claim} must be false")

    if errors:
        raise ApprovalCliError("; ".join(errors))
    return artifacts


def _load_artifact(path: Path) -> Any:
    try:
        if path.suffix in {".yaml", ".yml"}:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ApprovalCliError(f"Unable to read artifact {path}: {exc}") from exc


if __name__ == "__main__":
    sys.exit(main())
