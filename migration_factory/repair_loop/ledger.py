from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from migration_factory.contracts import SCHEMA_VERSION


FINAL_NOT_STARTED = "NOT_STARTED"


def ledger_path(run_dir: str | Path) -> Path:
    return Path(run_dir) / "repairs" / "repair_ledger.json"


def new_ledger(
    *,
    run_id: str,
    enabled: bool,
    auto_apply_enabled: bool,
    max_attempts: int,
    artifact_refs: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "enabled": enabled,
        "auto_apply_enabled": auto_apply_enabled,
        "max_attempts": max_attempts,
        "attempts": [],
        "final_status": FINAL_NOT_STARTED,
        "warnings": [],
        "errors": [],
        "artifact_refs": dict(artifact_refs or {}),
    }


def write_ledger(run_dir: str | Path, payload: dict[str, Any]) -> Path:
    path = ledger_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_ledger(run_dir: str | Path) -> dict[str, Any]:
    path = ledger_path(run_dir)
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"repair ledger must be an object: {path}")
    return payload


def append_attempt(ledger: dict[str, Any], attempt: dict[str, Any]) -> None:
    attempts = ledger.setdefault("attempts", [])
    if not isinstance(attempts, list):
        ledger["attempts"] = attempts = []
    attempts.append(attempt)


def base_attempt(
    *,
    attempt: int,
    failure_type: str,
    classification_ref: str,
    copilot_request_ref: str = "",
    copilot_response_ref: str = "",
    repair_plan_ref: str = "",
) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "failure_type": failure_type,
        "classification_ref": classification_ref,
        "copilot_request_ref": copilot_request_ref,
        "copilot_response_ref": copilot_response_ref,
        "repair_plan_ref": repair_plan_ref,
        "patch_gate_status": "NOT_EVALUATED",
        "deterministic_rule_id": "",
        "repair_proposal_checksum": "",
        "patch_ref": "",
        "patch_result_ref": "",
        "validation": {
            "build_status": "",
            "test_status": "",
            "h2_status": "",
        },
        "rollback": {
            "performed": False,
            "reason": "",
            "status": "",
        },
        "status": "PROPOSAL_WRITTEN",
    }


def write_patch_attempt_result(
    *,
    run_dir: str | Path,
    run_id: str,
    attempt: int,
    status: str,
    reason: str,
    rule_id: str = "",
    risk: str = "BLOCKED",
    paths: list[str] | None = None,
    before_hashes: dict[str, str] | None = None,
    after_hashes: dict[str, str] | None = None,
    validation_commands: list[list[str]] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> Path:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "attempt": attempt,
        "status": status,
        "reason": reason,
        "rule_id": rule_id,
        "risk": risk,
        "paths": list(paths or []),
        "before_hashes": dict(before_hashes or {}),
        "after_hashes": dict(after_hashes or {}),
        "validation_commands": list(validation_commands or []),
        "warnings": list(warnings or []),
        "errors": list(errors or []),
    }
    path = Path(run_dir) / "repairs" / f"patch_attempt_{attempt}_result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_patch_draft(
    *,
    run_dir: str | Path,
    attempt: int,
    payload: dict[str, Any],
) -> Path:
    path = Path(run_dir) / "repairs" / f"patch_draft_{attempt}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
