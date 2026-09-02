"""F5-T2: Repair context pack — checksum-bound input bundle for Primary Repair LLM.

Builds a model-safe RepairContextPack from failure evidence, job/command state,
upstream artifacts, prior repair attempts, reviewer notes, user comments,
and bounded source context.
Redacts secrets, absolute paths, raw env, endpoints, deployments, raw commands,
and sandbox paths.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from migration_factory.control_tower.domain.checksums import (
    sha256_canonical_json,
    sha256_hex,
    utc_now_text,
)
from migration_factory.repair_loop.failure_evidence import FailureEvidence
from migration_factory.repair_loop.patch_gate import _has_symlink_parent


FORBIDDEN_CONTEXT_KEYS: frozenset[str] = frozenset({
    "sandbox_path",
    "argv",
    "env",
    "raw_command",
    "endpoint",
    "deployment",
    "env_ref",
    "user_supplied_file_path",
    "filesystem_target",
})

MAX_SOURCE_CONTEXT_FILES = 3
MAX_LINES_BEFORE = 40
MAX_LINES_AFTER = 40
MAX_SOURCE_CONTEXT_CHARS = 40000
_BUILD_DESCRIPTOR_NAMES = (
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "package.json",
    "angular.json",
    "tsconfig.json",
)


@dataclass(frozen=True)
class RepairSourceContext:
    path: str
    content_checksum: str
    start_line: int
    end_line: int
    content: str
    reason_included: str
    # Backward-compatible content_checksum remains the full-file checksum.
    source_file_sha256: str = ""
    context_excerpt_sha256: str = ""
    context_is_complete: bool = False


def _normalize_and_check_path(
    file_path: str,
    sandbox_root: Path,
    *,
    allow_absolute: bool = True,
) -> Path | None:
    raw_path = str(file_path or "").strip()
    if not raw_path:
        return None
    windows_path = PureWindowsPath(raw_path)
    if not allow_absolute and (Path(raw_path).is_absolute() or windows_path.is_absolute() or windows_path.drive):
        return None
    candidate = (
        Path(raw_path)
        if Path(raw_path).is_absolute() or windows_path.is_absolute()
        else sandbox_root / PurePosixPath(raw_path.replace("\\", "/"))
    )
    if _has_symlink_parent(candidate, sandbox_root):
        return None
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    try:
        resolved.relative_to(sandbox_root)
    except ValueError:
        return None
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_bounded_source_context(
    *,
    sandbox_root: str | Path,
    compiler_errors: list[tuple[str, int]] | None = None,
    changed_files: tuple[str, ...] = (),
    build_context_files: tuple[str, ...] = (),
    max_files: int = MAX_SOURCE_CONTEXT_FILES,
    lines_before: int = MAX_LINES_BEFORE,
    lines_after: int = MAX_LINES_AFTER,
    max_chars: int = MAX_SOURCE_CONTEXT_CHARS,
    include_full_build_descriptors: bool = False,
) -> tuple[RepairSourceContext, ...]:
    sandbox = Path(sandbox_root).resolve()
    candidate_paths: dict[str, int] = {}
    if compiler_errors:
        for file_path, line_num in compiler_errors:
            if file_path not in candidate_paths:
                candidate_paths[file_path] = line_num
    for build_file in build_context_files:
        if build_file not in candidate_paths:
            candidate_paths[build_file] = 0
    for cf in changed_files:
        if cf not in candidate_paths:
            candidate_paths[cf] = 0
    contexts: list[RepairSourceContext] = []
    total_chars = 0
    for file_path in candidate_paths:
        if len(contexts) >= max_files:
            break
        normalized = _normalize_and_check_path(file_path, sandbox)
        if normalized is None:
            continue
        if not normalized.is_file():
            continue
        try:
            with normalized.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                lines = handle.read().splitlines(keepends=True)
        except (OSError, UnicodeDecodeError):
            continue
        if not lines:
            continue
        error_line = candidate_paths[file_path]
        is_build_descriptor = file_path in set(build_context_files)
        if include_full_build_descriptors and is_build_descriptor:
            start_line = 0
            end_line = len(lines)
        else:
            start_line = max(0, error_line - lines_before)
            end_line = min(len(lines), error_line + lines_after)
        excerpt = "".join(lines[start_line:end_line])
        if not excerpt.strip():
            continue
        excerpt_chars = len(excerpt)
        if total_chars + excerpt_chars > max_chars:
            remaining = max_chars - total_chars
            if remaining > 200:
                excerpt = excerpt[:remaining]
            else:
                continue
        source_file_sha256 = _sha256_file(normalized)
        context_excerpt_sha256 = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        context_is_complete = start_line == 0 and end_line == len(lines) and excerpt == "".join(lines)
        reason = (
            "compiler_error"
            if file_path in {e[0] for e in (compiler_errors or [])}
            else "build_configuration"
            if file_path in set(build_context_files)
            else "changed_file"
        )
        try:
            relative_path = normalized.relative_to(sandbox).as_posix()
        except ValueError:
            continue
        contexts.append(RepairSourceContext(
            path=relative_path,
            content_checksum=source_file_sha256,
            start_line=start_line + 1,
            end_line=end_line,
            content=excerpt,
            reason_included=reason,
            source_file_sha256=source_file_sha256,
            context_excerpt_sha256=context_excerpt_sha256,
            context_is_complete=context_is_complete,
        ))
        total_chars += len(excerpt)
    return tuple(contexts)


def find_relevant_build_context_files(
    *,
    sandbox_root: str | Path,
    working_directory: str | Path | None = None,
    module: str = "",
    tool: str = "",
) -> tuple[str, ...]:
    """Find bounded, repository-relative build descriptors for repair context."""
    sandbox = Path(sandbox_root).resolve()
    roots: list[Path] = []
    for candidate in (working_directory, module):
        if not candidate:
            continue
        path = Path(candidate)
        if not path.is_absolute():
            path = sandbox / path
        try:
            path = path.resolve()
            path.relative_to(sandbox)
        except ValueError:
            continue
        if path.is_file():
            path = path.parent
        if path.is_dir():
            ancestor = path
            while True:
                if ancestor not in roots:
                    roots.append(ancestor)
                if ancestor == sandbox or ancestor.parent == ancestor:
                    break
                ancestor = ancestor.parent
    if sandbox not in roots:
        roots.append(sandbox)

    names = list(_BUILD_DESCRIPTOR_NAMES)
    tool_text = str(tool or "").lower()
    if "maven" in tool_text or tool_text in {"mvn", "mvnw"}:
        names = ["pom.xml"]
    elif "gradle" in tool_text:
        names = [
            "build.gradle",
            "build.gradle.kts",
            "settings.gradle",
            "settings.gradle.kts",
        ]

    found: list[str] = []
    for root in roots:
        for name in names:
            candidate = root / name
            if not candidate.is_file() or candidate.is_symlink():
                continue
            try:
                relative = candidate.resolve().relative_to(sandbox).as_posix()
            except ValueError:
                continue
            if relative not in found:
                found.append(relative)
        for candidate in sorted(root.glob("tsconfig*.json")):
            if not candidate.is_file() or candidate.is_symlink():
                continue
            try:
                relative = candidate.resolve().relative_to(sandbox).as_posix()
            except ValueError:
                continue
            if relative not in found:
                found.append(relative)
    return tuple(found)


@dataclass(frozen=True)
class RepairContextPack:
    job_id: str
    stage_index: int
    command_id: str
    failure_source: str
    failure_evidence_checksum: str
    source_profile: str = ""
    target_profile: str = ""
    accepted_analysis_checksum: str = ""
    accepted_planning_checksum: str = ""
    prior_proposal_checksums: tuple[str, ...] = ()
    prior_reviewer_notes: tuple[str, ...] = ()
    user_comments: str = ""
    changed_files: tuple[str, ...] = ()
    safe_log_preview: str = ""
    base_repo_state_checksum: str = ""
    context_pack_checksum: str = ""
    prior_revision_ids: tuple[str, ...] = ()
    cycle_number: int = 0
    max_cycles: int = 3
    created_at: str = ""
    schema_version: str = "1.0.0"
    source_contexts: tuple[RepairSourceContext, ...] = ()


def compute_context_pack_checksum(pack: RepairContextPack) -> str:
    payload: dict[str, Any] = {
        "job_id": pack.job_id,
        "stage_index": pack.stage_index,
        "command_id": pack.command_id,
        "failure_source": pack.failure_source,
        "failure_evidence_checksum": pack.failure_evidence_checksum,
        "source_profile": pack.source_profile,
        "target_profile": pack.target_profile,
        "accepted_analysis_checksum": pack.accepted_analysis_checksum,
        "accepted_planning_checksum": pack.accepted_planning_checksum,
        "prior_proposal_checksums": list(sorted(pack.prior_proposal_checksums)),
        "prior_reviewer_notes": list(pack.prior_reviewer_notes),
        "user_comments": pack.user_comments,
        "changed_files": list(sorted(pack.changed_files)),
        "safe_log_preview": pack.safe_log_preview,
        "base_repo_state_checksum": pack.base_repo_state_checksum,
        "prior_revision_ids": list(sorted(pack.prior_revision_ids)),
        "cycle_number": pack.cycle_number,
        "max_cycles": pack.max_cycles,
        "source_contexts": [
            {
                "path": sc.path,
                "content_checksum": sc.content_checksum,
                "source_file_sha256": sc.source_file_sha256 or sc.content_checksum,
                "context_excerpt_sha256": sc.context_excerpt_sha256 or hashlib.sha256(sc.content.encode("utf-8")).hexdigest(),
                "context_is_complete": sc.context_is_complete,
                "start_line": sc.start_line,
                "end_line": sc.end_line,
                "line_start": sc.start_line,
                "line_end": sc.end_line,
                "reason_included": sc.reason_included,
            }
            for sc in pack.source_contexts
        ],
    }
    return sha256_canonical_json(payload)


def compute_base_repo_state_checksum(
    *,
    changed_files: tuple[str, ...] = (),
    file_checksums: dict[str, str] | None = None,
    source_profile: str = "",
    target_profile: str = "",
    accepted_artifact_checksums: tuple[str, ...] = (),
) -> str:
    payload: dict[str, Any] = {
        "changed_files": list(sorted(changed_files)),
        "file_checksums": dict(sorted((file_checksums or {}).items())),
        "source_profile": source_profile,
        "target_profile": target_profile,
        "accepted_artifact_checksums": list(sorted(accepted_artifact_checksums)),
    }
    return sha256_canonical_json(payload)


def _validate_context_forbidden_keys(pack: RepairContextPack) -> list[str]:
    failures: list[str] = []
    for forbidden in FORBIDDEN_CONTEXT_KEYS:
        if hasattr(pack, forbidden):
            value = getattr(pack, forbidden)
            if value:
                failures.append(f"forbidden key {forbidden!r} present in context pack")
    return failures


def build_repair_context_pack(
    *,
    failure_evidence: FailureEvidence,
    job_id: str = "",
    stage_index: int = 0,
    command_id: str = "",
    source_profile: str = "",
    target_profile: str = "",
    accepted_analysis_checksum: str = "",
    accepted_planning_checksum: str = "",
    prior_proposal_checksums: tuple[str, ...] | None = None,
    prior_reviewer_notes: tuple[str, ...] | None = None,
    user_comments: str = "",
    changed_files: tuple[str, ...] | None = None,
    file_checksums: dict[str, str] | None = None,
    accepted_artifact_checksums: tuple[str, ...] | None = None,
    prior_revision_ids: tuple[str, ...] | None = None,
    cycle_number: int = 0,
    max_cycles: int = 3,
    source_contexts: tuple[RepairSourceContext, ...] | None = None,
) -> RepairContextPack:
    resolved_job = job_id or failure_evidence.job_id
    resolved_stage = stage_index or failure_evidence.stage_index
    resolved_command = command_id or failure_evidence.command_id

    resolved_changed = tuple(changed_files) if changed_files else failure_evidence.changed_files
    resolved_profile_source = source_profile or failure_evidence.source_profile
    resolved_profile_target = target_profile or failure_evidence.target_profile

    base_repo_checksum = compute_base_repo_state_checksum(
        changed_files=resolved_changed,
        file_checksums=file_checksums,
        source_profile=resolved_profile_source,
        target_profile=resolved_profile_target,
        accepted_artifact_checksums=accepted_artifact_checksums or failure_evidence.accepted_artifact_checksums,
    )

    resolved_source_contexts = tuple(source_contexts or ())

    pack = RepairContextPack(
        job_id=resolved_job,
        stage_index=resolved_stage,
        command_id=resolved_command,
        failure_source=failure_evidence.failure_source.value,
        failure_evidence_checksum=failure_evidence.content_checksum,
        source_profile=resolved_profile_source,
        target_profile=resolved_profile_target,
        accepted_analysis_checksum=accepted_analysis_checksum,
        accepted_planning_checksum=accepted_planning_checksum,
        prior_proposal_checksums=tuple(sorted(prior_proposal_checksums or ())),
        prior_reviewer_notes=tuple(prior_reviewer_notes or ()),
        user_comments=user_comments,
        changed_files=resolved_changed,
        safe_log_preview=failure_evidence.safe_log_preview,
        base_repo_state_checksum=base_repo_checksum,
        prior_revision_ids=tuple(sorted(prior_revision_ids or ())),
        cycle_number=cycle_number,
        max_cycles=max_cycles,
        created_at=utc_now_text(),
        source_contexts=resolved_source_contexts,
    )

    context_checksum = compute_context_pack_checksum(pack)
    return RepairContextPack(
        job_id=pack.job_id,
        stage_index=pack.stage_index,
        command_id=pack.command_id,
        failure_source=pack.failure_source,
        failure_evidence_checksum=pack.failure_evidence_checksum,
        source_profile=pack.source_profile,
        target_profile=pack.target_profile,
        accepted_analysis_checksum=pack.accepted_analysis_checksum,
        accepted_planning_checksum=pack.accepted_planning_checksum,
        prior_proposal_checksums=pack.prior_proposal_checksums,
        prior_reviewer_notes=pack.prior_reviewer_notes,
        user_comments=pack.user_comments,
        changed_files=pack.changed_files,
        safe_log_preview=pack.safe_log_preview,
        base_repo_state_checksum=pack.base_repo_state_checksum,
        context_pack_checksum=context_checksum,
        prior_revision_ids=pack.prior_revision_ids,
        cycle_number=pack.cycle_number,
        max_cycles=pack.max_cycles,
        created_at=pack.created_at,
        schema_version=pack.schema_version,
        source_contexts=resolved_source_contexts,
    )


def is_context_pack_stale(
    pack: RepairContextPack,
    current_file_checksums: dict[str, str] | None = None,
    current_accepted_artifact_checksums: tuple[str, ...] | None = None,
) -> bool:
    new_repo_checksum = compute_base_repo_state_checksum(
        changed_files=pack.changed_files,
        file_checksums=current_file_checksums,
        source_profile=pack.source_profile,
        target_profile=pack.target_profile,
        accepted_artifact_checksums=current_accepted_artifact_checksums or (),
    )
    return new_repo_checksum != pack.base_repo_state_checksum


def context_pack_to_dict(pack: RepairContextPack) -> dict[str, Any]:
    result = {
        "job_id": pack.job_id,
        "stage_index": pack.stage_index,
        "command_id": pack.command_id,
        "failure_source": pack.failure_source,
        "failure_evidence_checksum": pack.failure_evidence_checksum,
        "source_profile": pack.source_profile,
        "target_profile": pack.target_profile,
        "accepted_analysis_checksum": pack.accepted_analysis_checksum,
        "accepted_planning_checksum": pack.accepted_planning_checksum,
        "prior_proposal_checksums": list(pack.prior_proposal_checksums),
        "prior_reviewer_notes": list(pack.prior_reviewer_notes),
        "user_comments": pack.user_comments,
        "changed_files": list(pack.changed_files),
        "safe_log_preview": pack.safe_log_preview,
        "base_repo_state_checksum": pack.base_repo_state_checksum,
        "context_pack_checksum": pack.context_pack_checksum,
        "prior_revision_ids": list(pack.prior_revision_ids),
        "cycle_number": pack.cycle_number,
        "max_cycles": pack.max_cycles,
        "created_at": pack.created_at,
        "schema_version": pack.schema_version,
    }
    if pack.source_contexts:
        result["source_contexts"] = [
            {
                "path": sc.path,
                "content_checksum": sc.content_checksum,
                "source_file_sha256": sc.source_file_sha256 or sc.content_checksum,
                "context_excerpt_sha256": sc.context_excerpt_sha256 or hashlib.sha256(sc.content.encode("utf-8")).hexdigest(),
                "context_is_complete": sc.context_is_complete,
                "start_line": sc.start_line,
                "end_line": sc.end_line,
                "line_start": sc.start_line,
                "line_end": sc.end_line,
                "content": sc.content,
                "reason_included": sc.reason_included,
            }
            for sc in pack.source_contexts
        ]
    return result
