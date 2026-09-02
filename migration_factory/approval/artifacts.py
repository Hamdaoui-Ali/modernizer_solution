from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from migration_factory.contracts import APPROVAL_DECISION_VALUES, SCHEMA_VERSION
from migration_factory.contracts.schema_validation import validate_against_schema


APPROVAL_DIR_NAME = "approval"
APPROVAL_DECISION_ARTIFACT = "approval_decision.json"
APPROVED_PLAN_LOCK_ARTIFACT = "approved_plan_lock.json"

_LOCKED_REQUIRED_ARTIFACTS = (
    "planning/migration_plan.yaml",
    "planning/migration_units.yaml",
    "assessment/assessment_report.json",
)
_LOCKED_OPTIONAL_ARTIFACTS = ("analysis/rewrite_plugin_plan.json",)


class ApprovalArtifactError(ValueError):
    """Raised when an approval artifact cannot be read, written, or validated."""


def write_approval_decision(
    run_dir: str | Path,
    run_id: str,
    decision: str,
    *,
    decided_by: str = "human",
    decided_at: str | None = None,
    comments: str = "",
    plan_lock_ref: str | None = None,
    source: str | None = None,
    artifact_refs: dict[str, str] | None = None,
) -> Path:
    artifact = _build_approval_decision(
        run_id=run_id,
        decision=decision,
        decided_by=decided_by,
        decided_at=decided_at,
        comments=comments,
        plan_lock_ref=plan_lock_ref,
        source=source,
        artifact_refs=artifact_refs,
    )
    _raise_for_schema_errors(artifact, "approval_decision.schema.json")

    path = _approval_dir(run_dir) / APPROVAL_DECISION_ARTIFACT
    _write_json(path, artifact)
    return path


def read_approval_decision(run_dir: str | Path) -> dict[str, Any]:
    return _read_json_object(_approval_dir(run_dir) / APPROVAL_DECISION_ARTIFACT)


def check_approval_decision(
    run_dir: str | Path, *, expected_run_id: str | None = None
) -> tuple[str, ...]:
    artifact_path = _approval_dir(run_dir) / APPROVAL_DECISION_ARTIFACT
    artifact, errors = _load_json_object_for_check(artifact_path)
    if errors:
        return errors

    check_errors = list(validate_against_schema(artifact, "approval_decision.schema.json"))
    if expected_run_id is not None and artifact.get("run_id") != expected_run_id:
        check_errors.append("approval_decision.json run_id mismatch")
    return tuple(check_errors)


def write_approved_plan_lock(run_dir: str | Path, run_id: str) -> Path:
    artifact = build_approved_plan_lock(run_dir, run_id)
    _raise_for_schema_errors(artifact, "approved_plan_lock.schema.json")

    path = _approval_dir(run_dir) / APPROVED_PLAN_LOCK_ARTIFACT
    _write_json(path, artifact)
    return path


def read_approved_plan_lock(run_dir: str | Path) -> dict[str, Any]:
    return _read_json_object(_approval_dir(run_dir) / APPROVED_PLAN_LOCK_ARTIFACT)


def build_approved_plan_lock(run_dir: str | Path, run_id: str) -> dict[str, Any]:
    root = Path(run_dir)
    locked_artifacts = [_artifact_hash(root, rel_path) for rel_path in _LOCKED_REQUIRED_ARTIFACTS]
    locked_artifacts.extend(
        _artifact_hash(root, rel_path)
        for rel_path in _LOCKED_OPTIONAL_ARTIFACTS
        if (root / rel_path).exists()
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "agent": "approval",
        "phase": "approval",
        "hash_algorithm": "sha256",
        "locked_artifacts": locked_artifacts,
        "artifact_refs": {"self": APPROVED_PLAN_LOCK_ARTIFACT},
    }


def check_approved_plan_lock(
    run_dir: str | Path, *, expected_run_id: str | None = None
) -> tuple[str, ...]:
    lock_path = _approval_dir(run_dir) / APPROVED_PLAN_LOCK_ARTIFACT
    lock, errors = _load_json_object_for_check(lock_path)
    if errors:
        return errors

    check_errors = list(validate_against_schema(lock, "approved_plan_lock.schema.json"))
    if expected_run_id is not None and lock.get("run_id") != expected_run_id:
        check_errors.append("approved_plan_lock.json run_id mismatch")
    if check_errors:
        return tuple(check_errors)

    try:
        current_lock = build_approved_plan_lock(run_dir, str(lock["run_id"]))
    except ApprovalArtifactError as exc:
        return (str(exc),)
    if lock.get("locked_artifacts") != current_lock["locked_artifacts"]:
        check_errors.append("approved_plan_lock.json artifact hashes do not match current run artifacts")
    return tuple(check_errors)


def _build_approval_decision(
    *,
    run_id: str,
    decision: str,
    decided_by: str,
    decided_at: str | None,
    comments: str,
    plan_lock_ref: str | None,
    source: str | None,
    artifact_refs: dict[str, str] | None,
) -> dict[str, Any]:
    if decision not in APPROVAL_DECISION_VALUES:
        raise ApprovalArtifactError(f"Unsupported approval decision: {decision}")

    refs = {"self": APPROVAL_DECISION_ARTIFACT}
    refs.update(artifact_refs or {})
    if plan_lock_ref is not None:
        refs["approved_plan_lock"] = plan_lock_ref

    artifact = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "agent": "human",
        "phase": "approval",
        "decision": decision,
        "decided_by": decided_by,
        "decided_at": decided_at or _utc_now(),
        "comments": comments,
        "plan_lock_ref": plan_lock_ref,
        "artifact_refs": refs,
    }
    if source is not None:
        artifact["source"] = source
    return artifact


def _artifact_hash(run_dir: Path, rel_path: str) -> dict[str, str]:
    path = run_dir / rel_path
    if not path.is_file():
        raise ApprovalArtifactError(f"Missing required lock artifact: {rel_path}")
    return {"path": rel_path, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _approval_dir(run_dir: str | Path) -> Path:
    return Path(run_dir) / APPROVAL_DIR_NAME


def _write_json(path: Path, artifact: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json_object(path: Path) -> dict[str, Any]:
    artifact, errors = _load_json_object_for_check(path)
    if errors:
        raise ApprovalArtifactError("; ".join(errors))
    return artifact


def _load_json_object_for_check(path: Path) -> tuple[dict[str, Any], tuple[str, ...]]:
    if not path.exists():
        return {}, (f"{path.name} missing",)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, (f"{path.name} invalid JSON: {exc}",)
    if not isinstance(payload, dict):
        return {}, (f"{path.name} must be JSON object",)
    return payload, ()


def _raise_for_schema_errors(payload: dict[str, Any], schema_name: str) -> None:
    errors = validate_against_schema(payload, schema_name)
    if errors:
        raise ApprovalArtifactError("; ".join(errors))


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
