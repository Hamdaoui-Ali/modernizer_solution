from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from migration_factory.contracts import SCHEMA_VERSION


SECURITY_PATH_RE = re.compile(r"(security|jwt|keystore|oauth|auth)", re.IGNORECASE)
SOURCE_EXTENSIONS = {".java", ".kt", ".groovy", ".xml", ".properties", ".yml", ".yaml"}
GENERATED_PARTS = {"target", "build", "generated-sources", "generated"}


def scan_openrewrite_diff(
    *,
    run_id: str,
    diff_text: str = "",
    changed_files: list[str] | None = None,
    deleted_files: list[str] | None = None,
    planned_pom_changes: list[str] | None = None,
) -> dict[str, Any]:
    parsed = _parse_unified_diff(diff_text)
    changed = sorted(set(changed_files or []) | set(parsed["changed_files"]))
    deleted = sorted(set(deleted_files or []) | set(parsed["deleted_files"]))
    added_lines = parsed["added_lines"]
    removed_lines = parsed["removed_lines"]

    high_risk: list[str] = []
    warnings: list[str] = []
    security_changes: list[str] = []
    business_logic_changes: list[str] = []
    pom_changes: list[dict[str, str]] = []
    source_changes: list[str] = []

    for path in changed:
        if _is_generated(path):
            warnings.append(f"generated output ignored: {path}")
            continue
        if Path(path).name == "pom.xml":
            pom_changes.append({"path": path, "classification": "planned" if _planned(path, planned_pom_changes) else "review"})
            if not _planned(path, planned_pom_changes) and _contains_dependency_removal(removed_lines):
                high_risk.append(f"POM dependency removal or scope weakening requires review: {path}")
            continue
        if Path(path).suffix in SOURCE_EXTENSIONS:
            source_changes.append(path)

    for path in deleted:
        if _is_generated(path):
            continue
        if Path(path).suffix in SOURCE_EXTENSIONS:
            high_risk.append(f"Deleted non-generated source/config file: {path}")

    security_lines = [line for line in [*added_lines, *removed_lines] if _security_line(line)]
    if any(SECURITY_PATH_RE.search(path) for path in changed) or security_lines:
        security_changes.extend(sorted(path for path in changed if SECURITY_PATH_RE.search(path)))
        high_risk.append("Spring Security/authentication behavior change requires human review.")
    if any("permitAll" in line for line in added_lines):
        high_risk.append("Added or broadened permitAll detected.")
    if any(_business_logic_line(line) for line in [*added_lines, *removed_lines]):
        business_logic_changes.append("method-body or controller/service/repository logic changed")

    if business_logic_changes and not _only_mechanical_jakarta(added_lines, removed_lines):
        high_risk.append("Business logic change outside known mechanical migration.")

    if high_risk:
        status = "BLOCKED" if any("permitAll" in item or "Security" in item for item in high_risk) else "HIGH_RISK"
        risk_level = "BLOCKED" if status == "BLOCKED" else "HIGH"
    elif warnings:
        status = "WARNING"
        risk_level = "MEDIUM"
    else:
        status = "LOW_RISK"
        risk_level = "LOW"

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "status": status,
        "risk_level": risk_level,
        "changed_files": changed,
        "deleted_files": deleted,
        "pom_changes": pom_changes,
        "source_changes": source_changes,
        "security_behavior_changes": security_changes,
        "business_logic_changes": business_logic_changes,
        "high_risk_changes": high_risk,
        "warnings": warnings,
        "recommended_next_step": "human_review_required" if high_risk else "continue",
        "copilot_repair_recommended": bool(high_risk),
        "requires_human_review": bool(high_risk),
        "artifact_refs": {},
    }


def write_openrewrite_diff_safety_report(*, run_dir: str | Path, report: dict[str, Any]) -> Path:
    path = Path(run_dir) / "transformation" / "openrewrite_diff_safety_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _parse_unified_diff(diff_text: str) -> dict[str, Any]:
    changed: list[str] = []
    deleted: list[str] = []
    added_lines: list[str] = []
    removed_lines: list[str] = []
    current: str | None = None
    for raw in diff_text.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                current = parts[3][2:] if parts[3].startswith("b/") else parts[3]
                changed.append(current)
        elif line.startswith("deleted file mode") and current:
            deleted.append(current)
        elif line.startswith("+++ b/"):
            current = line[6:]
            changed.append(current)
        elif line.startswith("+") and not line.startswith("+++"):
            added_lines.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            removed_lines.append(line[1:])
    return {
        "changed_files": changed,
        "deleted_files": deleted,
        "added_lines": added_lines,
        "removed_lines": removed_lines,
    }


def _is_generated(path: str) -> bool:
    return bool(set(Path(path).parts) & GENERATED_PARTS)


def _planned(path: str, planned_pom_changes: list[str] | None) -> bool:
    planned = planned_pom_changes or []
    return any(item in path or item in Path(path).name for item in planned)


def _contains_dependency_removal(lines: list[str]) -> bool:
    text = "\n".join(lines).lower()
    return "<dependency>" in text or "<scope>compile</scope>" in text or "<scope>runtime</scope>" in text


def _security_line(line: str) -> bool:
    lowered = line.lower()
    return any(token in lowered for token in ("securityfilterchain", "permitall", "csrf", "cors", "jwt", "resourceserver", "keystore", "websecurityconfigureradapter"))


def _business_logic_line(line: str) -> bool:
    lowered = line.lower()
    return any(token in lowered for token in ("public ", "private ", "protected ", "@getmapping", "@postmapping", "repository", "controller", "service"))


def _only_mechanical_jakarta(added_lines: list[str], removed_lines: list[str]) -> bool:
    combined = "\n".join([*added_lines, *removed_lines]).strip()
    if not combined:
        return False
    return all(("javax." in line or "jakarta." in line or not line.strip()) for line in [*added_lines, *removed_lines])
