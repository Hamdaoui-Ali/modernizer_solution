from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json


LEDGER_SCHEMA_VERSION = "1.0"


class LedgerStatus:
    INITIALIZED = "initialized"
    UNIT_IN_PROGRESS = "unit_in_progress"
    AWAITING_BUILD_AGENT = "awaiting_build_agent"
    BUILD_VALIDATED = "build_validated"
    BLOCKED = "blocked"
    COMPLETED = "completed"


class BuildValidationStatus:
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class LedgerError(Exception):
    pass


def initialize_ledger(
    ledger_file: str | Path,
    *,
    migration_id: str,
    migration_name: str | None,
    total_units: int,
    target_path: str | Path,
) -> dict[str, Any]:
    now = _now()
    ledger = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "migration_id": migration_id,
        "migration_name": migration_name,
        "target_path": str(Path(target_path).expanduser().resolve()),
        "status": LedgerStatus.INITIALIZED,
        "current_unit": None,
        "next_unit_index": 0,
        "total_units": total_units,
        "completed_units": [],
        "blocked_unit": None,
        "build_validation": {
            "required": False,
            "status": BuildValidationStatus.NOT_REQUIRED,
        },
        "test_validation": {
            "required": False,
            "status": None,
        },
        "units": {},
        "created_at": now,
        "updated_at": now,
    }
    save_ledger(ledger_file, ledger)
    return ledger


def load_ledger(ledger_file: str | Path) -> dict[str, Any]:
    path = Path(ledger_file)
    if not path.is_file():
        raise LedgerError(f"Ledger file does not exist: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LedgerError(f"Ledger file is not valid JSON: {path}") from exc


def save_ledger(ledger_file: str | Path, ledger: dict[str, Any]) -> None:
    path = Path(ledger_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger["updated_at"] = _now()
    path.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def mark_unit_in_progress(
    ledger_file: str | Path,
    *,
    unit_id: str,
    unit_index: int,
    title: str | None,
) -> dict[str, Any]:
    ledger = load_ledger(ledger_file)
    unit = _unit_entry(ledger, unit_id)
    unit.update(
        {
            "id": unit_id,
            "title": title,
            "index": unit_index,
            "status": LedgerStatus.UNIT_IN_PROGRESS,
            "started_at": unit.get("started_at") or _now(),
            "finished_at": None,
        }
    )
    ledger["status"] = LedgerStatus.UNIT_IN_PROGRESS
    ledger["current_unit"] = unit_id
    ledger["next_unit_index"] = unit_index
    ledger["build_validation"] = {
        "required": False,
        "status": BuildValidationStatus.NOT_REQUIRED,
    }
    save_ledger(ledger_file, ledger)
    return ledger


def mark_unit_awaiting_build(
    ledger_file: str | Path,
    *,
    unit_id: str,
    expected_files: list[str] | None = None,
    checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ledger = load_ledger(ledger_file)
    _require_current_unit(ledger, unit_id)
    unit = _unit_entry(ledger, unit_id)
    unit.update(
        {
            "status": LedgerStatus.AWAITING_BUILD_AGENT,
            "finished_at": _now(),
            "expected_files": expected_files or [],
            "checks": checks or [],
        }
    )
    ledger["status"] = LedgerStatus.AWAITING_BUILD_AGENT
    ledger["build_validation"] = {
        "required": True,
        "status": BuildValidationStatus.PENDING,
        "unit_id": unit_id,
    }
    save_ledger(ledger_file, ledger)
    return ledger


def mark_build_passed(
    ledger_file: str | Path,
    *,
    result_kind: str,
    message: str,
    matched_line: str | None = None,
    exit_code: int | None = None,
    warnings: list[str] | None = None,
    command: list[str] | None = None,
    cwd: str | Path | None = None,
    command_duration_seconds: float | None = None,
) -> dict[str, Any]:
    ledger = load_ledger(ledger_file)
    unit_id = ledger.get("current_unit")
    if not unit_id:
        raise LedgerError("Cannot mark build passed because ledger has no current_unit")

    unit = _unit_entry(ledger, unit_id)
    unit["status"] = LedgerStatus.BUILD_VALIDATED
    if unit_id not in ledger["completed_units"]:
        ledger["completed_units"].append(unit_id)

    ledger["status"] = LedgerStatus.BUILD_VALIDATED
    ledger["next_unit_index"] = int(unit.get("index", 0)) + 1
    ledger["build_validation"] = {
        "required": True,
        "status": BuildValidationStatus.PASSED,
        "unit_id": unit_id,
        "validated_at": _now(),
        "result_kind": result_kind,
        "message": message,
        "matched_line": matched_line,
        "exit_code": exit_code,
        "warnings": warnings or [],
        "command": command or [],
        "cwd": str(cwd) if cwd is not None else None,
        "command_duration_seconds": command_duration_seconds,
    }
    save_ledger(ledger_file, ledger)
    return ledger


def mark_build_failed(
    ledger_file: str | Path,
    *,
    result_kind: str,
    message: str,
    error_contract_path: str | Path | None,
    matched_line: str | None = None,
    exit_code: int | None = None,
    warnings: list[str] | None = None,
    command: list[str] | None = None,
    cwd: str | Path | None = None,
    command_duration_seconds: float | None = None,
) -> dict[str, Any]:
    ledger = load_ledger(ledger_file)
    unit_id = ledger.get("current_unit")
    if not unit_id:
        raise LedgerError("Cannot mark build failed because ledger has no current_unit")

    unit = _unit_entry(ledger, unit_id)
    unit["status"] = LedgerStatus.BLOCKED

    ledger["status"] = LedgerStatus.BLOCKED
    ledger["blocked_unit"] = unit_id
    ledger["build_validation"] = {
        "required": True,
        "status": BuildValidationStatus.FAILED,
        "unit_id": unit_id,
        "validated_at": _now(),
        "result_kind": result_kind,
        "message": message,
        "matched_line": matched_line,
        "exit_code": exit_code,
        "error_contract_path": str(error_contract_path) if error_contract_path else None,
        "warnings": warnings or [],
        "command": command or [],
        "cwd": str(cwd) if cwd is not None else None,
        "command_duration_seconds": command_duration_seconds,
    }
    save_ledger(ledger_file, ledger)
    return ledger


def _unit_entry(ledger: dict[str, Any], unit_id: str) -> dict[str, Any]:
    units = ledger.setdefault("units", {})
    unit = units.setdefault(unit_id, {})
    return unit


def _require_current_unit(ledger: dict[str, Any], unit_id: str) -> None:
    current_unit = ledger.get("current_unit")
    if current_unit != unit_id:
        raise LedgerError(f"Ledger current_unit is {current_unit!r}, expected {unit_id!r}")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
