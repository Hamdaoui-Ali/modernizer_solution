import hashlib
import json
from pathlib import Path
from typing import Dict

from path_guard import IGNORED_DIR_NAMES, is_ignored_generated_path


Snapshot = Dict[str, str]


def snapshot_tree(root: str | Path) -> Snapshot:
    base = Path(root).resolve()
    snapshot: Snapshot = {}
    if not base.exists():
        return snapshot

    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        relative_path = path.relative_to(base).as_posix()
        if is_ignored_generated_path(relative_path):
            continue
        snapshot[relative_path] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _diff(before: Snapshot, after: Snapshot) -> list[dict]:
    changes = []
    for path in sorted(set(before) | set(after)):
        before_hash = before.get(path)
        after_hash = after.get(path)
        if before_hash == after_hash:
            continue
        if before_hash is None:
            change_type = "added"
        elif after_hash is None:
            change_type = "deleted"
        else:
            change_type = "modified"
        changes.append({"path": path, "change_type": change_type})
    return changes


def _is_allowed_write(relative_path: str, allowed_write_roots: list[str]) -> bool:
    return any(
        relative_path == root or relative_path.startswith(f"{root}/")
        for root in allowed_write_roots
    )


def build_read_only_verification(
    *,
    run_id: str,
    legacy_root: str | Path,
    modernized_root: str | Path,
    before_legacy: Snapshot,
    before_modernized: Snapshot,
    output_dir: str | Path,
) -> dict:
    legacy_after = snapshot_tree(legacy_root)
    modernized_after = snapshot_tree(modernized_root)
    allowed_write_roots = [
        Path(output_dir).resolve().relative_to(Path(modernized_root).resolve()).as_posix()
    ]

    legacy_changes = _diff(before_legacy, legacy_after)
    modernized_changes = _diff(before_modernized, modernized_after)
    modernized_violations = [
        change for change in modernized_changes if not _is_allowed_write(change["path"], allowed_write_roots)
    ]
    violations = [{**change, "tree": "legacy"} for change in legacy_changes]
    violations.extend({**change, "tree": "modernized"} for change in modernized_violations)

    source_modified = bool(violations)
    artifact_rel = f"{allowed_write_roots[0]}/read_only_verification.json"
    return {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "agent": "analysis_agent",
        "phase": "analysis",
        "status": "FAIL" if source_modified else "PASS",
        "paths": {
            "legacy_root": str(Path(legacy_root).resolve()),
            "modernized_root": str(Path(modernized_root).resolve()),
            "artifact": artifact_rel,
        },
        "allowed_write_roots": allowed_write_roots,
        "checks": {
            "legacy_tree_unchanged": not legacy_changes,
            "modernized_source_unchanged": not modernized_violations,
            "ignored_generated_paths": sorted(f"{name}/" for name in IGNORED_DIR_NAMES),
        },
        "violations": violations,
        "source_modified": source_modified,
        "artifact_refs": {"self": artifact_rel},
    }


def write_read_only_verification(context, before_legacy: Snapshot, before_modernized: Snapshot) -> dict:
    verification = build_read_only_verification(
        run_id=context.run_id,
        legacy_root=context.legacy_app_path,
        modernized_root=context.modernized_app_path,
        before_legacy=before_legacy,
        before_modernized=before_modernized,
        output_dir=context.output_dir,
    )
    output_path = context.get_output_path("read_only_verification.json")
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(verification, handle, indent=4)
    return verification
