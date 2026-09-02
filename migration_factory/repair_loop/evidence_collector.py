from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from migration_factory.agents.failure_classifier import classify_failure, write_failure_classification
from migration_factory.copilot_repair.request_builder import build_repair_request


SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)(password|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)


def collect_failure_evidence(
    *,
    run_id: str,
    run_dir: str | Path,
    sandbox_path: str | Path | None,
    artifact_refs: dict[str, str],
    transform_log_path: str = "",
    build_status: str = "",
    test_status: str = "",
    h2_startup_report: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    run_path = Path(run_dir)
    openrewrite_report = _read_json_ref(artifact_refs, "openrewrite_diff_safety_report")
    h2_report = h2_startup_report or _read_json_ref(artifact_refs, "h2_startup_report")
    evidence = {
        "build_status": build_status,
        "test_status": test_status,
        "transform_log_tail": _tail_file(transform_log_path),
        "test_report_summary": _read_json_ref(artifact_refs, "post_transform_test_report"),
        "test_log_tail": _tail_file(artifact_refs.get("post_transform_test_log", "")),
        "build_error_contract": _latest_json(run_path / "build"),
        "h2_startup_report": h2_report,
        "openrewrite_diff_safety_report": openrewrite_report,
        "pom_excerpt": _pom_excerpt(sandbox_path),
        "previous_repair_attempts": _read_json(run_path / "repairs" / "repair_ledger.json").get("attempts", []),
        "safety_instructions": [
            "Copilot output is untrusted and advisory until deterministic gates pass.",
            "Never mutate legacy source.",
            "Patch application is sandbox-only.",
            "SQL Server, production DB, endpoint smoke, deployment, PR creation, and merge are out of scope.",
            "Spring Security changes require human review.",
        ],
    }
    evidence_text = json.dumps(evidence, sort_keys=True)
    classification = classify_failure(
        run_id=run_id,
        evidence_text=evidence_text,
        openrewrite_report=openrewrite_report,
        h2_report=h2_report,
    )
    classification_path = write_failure_classification(run_dir=run_path, report=classification)
    refs = dict(artifact_refs)
    refs["failure_classification"] = str(classification_path)
    request = build_repair_request(
        run_dir=run_path,
        run_id=run_id,
        failure_classification=classification,
        artifact_refs=refs,
        openrewrite_diff_safety=openrewrite_report,
        h2_startup_report=h2_report,
    )
    request["evidence"] = _redact(evidence)
    request["mode"] = "proposal_only"
    request_path = run_path / "failures" / "copilot_repair_request.json"
    request_path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return classification, classification_path, request


def _read_json_ref(artifact_refs: dict[str, str], key: str) -> dict[str, Any]:
    ref = artifact_refs.get(key, "")
    return _read_json(Path(ref)) if ref else {}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _latest_json(directory: Path) -> dict[str, Any]:
    if not directory.is_dir():
        return {}
    candidates = sorted(directory.rglob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for candidate in candidates[:5]:
        payload = _read_json(candidate)
        if payload:
            return payload
    return {}


def _tail_file(path_value: str, max_chars: int = 4000) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return _redact_text(text[-max_chars:])


def _pom_excerpt(sandbox_path: str | Path | None, max_chars: int = 6000) -> str:
    if not sandbox_path:
        return ""
    pom = Path(sandbox_path) / "pom.xml"
    try:
        text = pom.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [
        line
        for line in text.splitlines()
        if any(token in line for token in ("<parent>", "<groupId>", "<artifactId>", "<version>", "<scope>", "<spring-boot.version>"))
    ]
    return _redact_text("\n".join(lines)[-max_chars:])


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(text: str) -> str:
    clean = text
    for pattern in SECRET_PATTERNS:
        clean = pattern.sub("[REDACTED]", clean)
    home = str(Path.home())
    if home:
        clean = clean.replace(home, "%USERPROFILE%").replace(home.replace("\\", "/"), "%USERPROFILE%")
    return clean
