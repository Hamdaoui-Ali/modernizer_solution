from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from migration_factory.contracts import SCHEMA_VERSION
from migration_factory.copilot_repair.request_builder import COPILOT_RESPONSE_TEMPLATE
from migration_factory.copilot_repair.skill_validator import AGENT_PATH, SKILL_PATHS


RESPONSE_SCHEMA_SOURCE = Path("migration_factory/contracts/schemas/copilot_repair_response.schema.json")
RESPONSE_SCHEMA_EVIDENCE_PATH = Path("evidence/copilot_repair_response.schema.json")
RESPONSE_TEMPLATE_EVIDENCE_PATH = Path("evidence/copilot_repair_response.template.json")

SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)\b[A-Za-z_]*(?:TOKEN|SECRET|PASSWORD|CREDENTIAL|API_KEY)[A-Za-z_]*\s*=\s*[^\s]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)
BLOCKED_DIR_NAMES = {".git", "target", "build", "node_modules", ".mvn", ".gradle"}


@dataclass(frozen=True)
class EvidenceSession:
    session_dir: Path
    manifest_path: Path
    manifest: dict[str, Any]


def create_evidence_session(
    *,
    repo_root: str | Path,
    run_dir: str | Path,
    run_id: str,
    evidence: dict[str, Any],
) -> EvidenceSession:
    root = Path(repo_root)
    run_path = Path(run_dir).resolve()
    session_dir = _next_session_dir(run_path)
    session_dir.mkdir(parents=True, exist_ok=False)

    _copy_agent_and_skills(root, session_dir)
    evidence_dir = session_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    request_path = evidence_dir / "copilot_repair_request.json"
    request_path.write_text(json.dumps(_redact(evidence), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _copy_response_schema(root, session_dir)
    _write_response_template(session_dir)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_dir.name,
        "created_at": _utc_now(),
        "run_id": run_id,
        "files": _relative_files(session_dir),
        "before_hashes": snapshot_hashes(session_dir),
        "after_hashes": {},
        "unexpected_mutations": [],
        "redaction_applied": True,
    }
    manifest_path = session_dir / "evidence_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return EvidenceSession(session_dir=session_dir, manifest_path=manifest_path, manifest=manifest)


def finalize_evidence_session(session_dir: str | Path, *, strict: bool = True) -> dict[str, Any]:
    session_path = Path(session_dir)
    manifest_path = session_path / "evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    before = dict(manifest.get("before_hashes", {}) or {})
    after = snapshot_hashes(session_path)
    unexpected = _unexpected_mutations(before, after)
    manifest["after_hashes"] = after
    manifest["unexpected_mutations"] = unexpected
    if unexpected:
        manifest.setdefault("warnings", []).append("Copilot evidence session mutated during invocation.")
        if strict:
            manifest.setdefault("errors", []).append("Strict containment detected unexpected evidence session mutation.")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def snapshot_hashes(directory: str | Path) -> dict[str, str]:
    root = Path(directory)
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if any(part in BLOCKED_DIR_NAMES for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        hashes[rel] = _sha256(path)
    return hashes


def _next_session_dir(run_dir: Path) -> Path:
    base = run_dir / "copilot"
    base.mkdir(parents=True, exist_ok=True)
    index = 1
    while (base / f"evidence_session_{index}").exists():
        index += 1
    return base / f"evidence_session_{index}"


def _copy_agent_and_skills(repo_root: Path, session_dir: Path) -> None:
    for rel in (AGENT_PATH, *SKILL_PATHS):
        source = repo_root / rel
        if not source.is_file():
            continue
        destination = session_dir / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _copy_response_schema(repo_root: Path, session_dir: Path) -> None:
    source = repo_root / RESPONSE_SCHEMA_SOURCE
    if not source.is_file():
        return
    destination = session_dir / RESPONSE_SCHEMA_EVIDENCE_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _write_response_template(session_dir: Path) -> None:
    destination = session_dir / RESPONSE_TEMPLATE_EVIDENCE_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(COPILOT_RESPONSE_TEMPLATE, indent=2) + "\n", encoding="utf-8")


def _relative_files(session_dir: Path) -> list[str]:
    return sorted(path.relative_to(session_dir).as_posix() for path in session_dir.rglob("*") if path.is_file())


def _unexpected_mutations(before: dict[str, str], after: dict[str, str]) -> list[dict[str, str]]:
    mutations: list[dict[str, str]] = []
    all_paths = sorted(set(before) | set(after))
    for rel in all_paths:
        if rel in {"evidence_manifest.json", "copilot_invocation_debug.json"}:
            continue
        old = before.get(rel)
        new = after.get(rel)
        if old == new:
            continue
        if old is None:
            status = "created"
        elif new is None:
            status = "deleted"
        else:
            status = "modified"
        mutations.append({"path": rel, "status": status})
    return mutations


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        text = value
        for pattern in SECRET_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        home = str(Path.home())
        if home:
            text = text.replace(home, "%USERPROFILE%").replace(home.replace("\\", "/"), "%USERPROFILE%")
        return text
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
