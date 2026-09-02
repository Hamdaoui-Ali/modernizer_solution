"""F5: Repair review-chain producer — extends the F2 review-chain pattern for repair.

Deterministic repair artifact -> Primary Repair LLM (PROPOSER) -> Reviewer Repair LLM (REVIEWER)
-> Final reviewed repair diff artifact.

Core rule: A model reviews another model for repair. Reviewer is mandatory.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import re
import shlex
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from migration_factory.control_tower.application.v2_assistant_model_client import (
    V2AssistantModelClient,
)
from migration_factory.control_tower.application.redaction import redact_model_summary
from migration_factory.control_tower.application.v2_model_role_router import V2ModelRole
from migration_factory.control_tower.application.v2_review_chain_contracts import (
    _check_forbidden_fields,
    _check_execution_instruction,
)
from migration_factory.control_tower.domain.checksums import (
    sha256_canonical_json,
    utc_now_text,
)
from migration_factory.repair_loop.failure_evidence import (
    FailureEvidence,
    FailureSource,
    failure_evidence_to_dict,
)
from migration_factory.repair_loop.repair_context import (
    RepairContextPack,
    _normalize_and_check_path,
    compute_base_repo_state_checksum,
    compute_context_pack_checksum,
    context_pack_to_dict,
)
from migration_factory.repair_loop.repair_intelligence import run_repair_intelligence_preflight
from migration_factory.control_tower.application.target_version_update import inspect_pom_for_coordinate


class RepairReviewChainProductionError(RuntimeError):
    pass


_DIAGNOSTIC_SECRET_VALUE_RE = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|token|password|secret|private[_-]?key|credential)[\"']?\s*[:=]\s*)([\"'][^\"']*[\"']|[^,\s}]+)"
)

_UNIFIED_HUNK_HEADER_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$"
)

_DIFF_METADATA_PREFIXES = (
    "index ", "old mode ", "new mode ", "new file mode ",
    "deleted file mode ", "similarity index ", "dissimilarity index ",
    "rename from ", "rename to ",
)


def _line_body(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith(("\n", "\r")):
        return line[:-1]
    return line


def _eol_normalize(text: str) -> str:
    """Normalize CRLF and CR to LF for structured-edit preimage comparison."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _relative_diff_path(raw: str) -> str:
    value = raw.split("\t", 1)[0].strip().strip('"')
    if value in {"/dev/null", ""}:
        return value
    normalized = value.replace("\\", "/")
    if normalized.startswith(("a/", "b/")):
        normalized = normalized[2:]
    return normalized


def _path_errors(path: str) -> list[str]:
    normalized = str(path).replace("\\", "/")
    pure = PurePosixPath(normalized)
    win = PureWindowsPath(str(path))
    if not normalized or normalized == "/dev/null":
        return []
    errors: list[str] = []
    if normalized.startswith(("/", "//")) or win.is_absolute() or re.match(r"^[A-Za-z]:", str(path)):
        errors.append(f"absolute patch path rejected: {path}")
    if ".." in pure.parts:
        errors.append(f"path traversal rejected: {path}")
    return errors


def _strict_parse_unified_diff(diff: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse the candidate grammar without discarding malformed hunk lines."""
    if not diff or not diff.strip():
        return [], ["candidate diff is empty"]
    if "```" in diff:
        return [], ["candidate diff contains Markdown fences"]
    if not diff.lstrip().startswith("diff --git "):
        return [], ["candidate diff must start with diff --git"]
    lines = diff.splitlines(keepends=True)
    sections: list[dict[str, Any]] = []
    failures: list[str] = []
    current: dict[str, Any] | None = None
    hunk: dict[str, Any] | None = None

    def finish_hunk() -> None:
        nonlocal hunk
        if hunk is None:
            return
        old_actual = sum(1 for line in hunk["body"] if line.startswith((" ", "-")))
        new_actual = sum(1 for line in hunk["body"] if line.startswith((" ", "+")))
        if old_actual != hunk["old_count"] or new_actual != hunk["new_count"]:
            failures.append(
                f"hunk {hunk['header']} count mismatch: declared old/new "
                f"{hunk['old_count']}/{hunk['new_count']}, body has {old_actual}/{new_actual}"
            )
        hunk = None

    def finish_section() -> None:
        nonlocal current
        finish_hunk()
        if current is None:
            return
        if not current.get("old_path") or not current.get("new_path"):
            failures.append("file section is missing --- or +++ headers")
        for marker in ("old_path", "new_path"):
            failures.extend(_path_errors(str(current.get(marker) or "")))
        if not current.get("hunks"):
            failures.append(f"file section {current.get('path', '<unknown>')} has no hunk")
        sections.append(current)
        current = None

    for raw in lines:
        line = _line_body(raw)
        if line.startswith("diff --git "):
            finish_section()
            try:
                parts = shlex.split(line[len("diff --git "):])
            except ValueError:
                parts = []
            if len(parts) != 2:
                failures.append(f"malformed diff --git header: {line}")
                current = {"path": "", "old_path": "", "new_path": "", "hunks": []}
                continue
            old_path = _relative_diff_path(parts[0])
            new_path = _relative_diff_path(parts[1])
            failures.extend(_path_errors(old_path))
            failures.extend(_path_errors(new_path))
            if old_path != new_path and "/dev/null" not in {old_path, new_path}:
                failures.append(f"file section has mismatched paths: {old_path} != {new_path}")
            current = {
                "path": new_path if new_path != "/dev/null" else old_path,
                "header_old_path": old_path,
                "header_new_path": new_path,
                "old_path": None,
                "new_path": None,
                "hunks": [],
            }
            hunk = None
            continue

        if current is None:
            if line.strip():
                failures.append(f"prose or metadata outside diff section: {line[:120]}")
            continue
        if line.startswith("--- "):
            current["old_path"] = _relative_diff_path(line[4:])
            continue
        if line.startswith("+++ "):
            current["new_path"] = _relative_diff_path(line[4:])
            continue
        match = _UNIFIED_HUNK_HEADER_RE.match(line)
        if match:
            finish_hunk()
            old_start, old_count, new_start, new_count, section = match.groups()
            hunk = {
                "header": line,
                "old_start": int(old_start),
                "old_count": int(old_count or 1),
                "new_start": int(new_start),
                "new_count": int(new_count or 1),
                "section": section,
                "body": [],
            }
            current["hunks"].append(hunk)
            continue
        if hunk is None:
            if line.strip() and not line.startswith(_DIFF_METADATA_PREFIXES):
                failures.append(f"unexpected content outside hunk: {line[:120]}")
            continue
        if line == "\\ No newline at end of file":
            hunk["body"].append(line)
        elif line.startswith((" ", "+", "-")):
            hunk["body"].append(line)
        else:
            failures.append(f"unprefixed line inside hunk {hunk['header']}: {line[:120]}")

    finish_section()
    if not sections:
        failures.append("candidate diff contains no valid file section")
    seen_paths: set[str] = set()
    for section in sections:
        if section.get("path") in seen_paths:
            failures.append(f"duplicate file section for {section.get('path')}")
        seen_paths.add(str(section.get("path") or ""))
        if section.get("old_path") != section.get("header_old_path") and section.get("old_path") != "/dev/null":
            failures.append(f"--- path does not match diff --git path for {section['path']}")
        if section.get("new_path") != section.get("header_new_path") and section.get("new_path") != "/dev/null":
            failures.append(f"+++ path does not match diff --git path for {section['path']}")
    return sections, failures


def _normalize_unified_diff_hunk_counts(unified_diff: str) -> str:
    """Correct unified-diff hunk counts without changing patch body bytes."""
    def split_line_ending(line: str) -> tuple[str, str]:
        if line.endswith("\r\n"):
            return line[:-2], "\r\n"
        if line.endswith(("\n", "\r")):
            return line[:-1], line[-1:]
        return line, ""

    lines = unified_diff.splitlines(keepends=True)
    normalized: list[str] = []
    changed = False
    index = 0

    while index < len(lines):
        line = lines[index]
        body, ending = split_line_ending(line)
        match = _UNIFIED_HUNK_HEADER_RE.match(body)
        if match is None:
            normalized.append(line)
            index += 1
            continue

        old_start, old_count, new_start, new_count, section = match.groups()
        old_actual = 0
        new_actual = 0
        body_end = index + 1
        while body_end < len(lines):
            candidate = lines[body_end]
            candidate_body, _ = split_line_ending(candidate)
            if _UNIFIED_HUNK_HEADER_RE.match(candidate_body):
                break
            if candidate_body.startswith("diff --git "):
                break
            if candidate_body.startswith("\\ No newline at end of file"):
                body_end += 1
                continue
            if candidate_body.startswith(" "):
                old_actual += 1
                new_actual += 1
            elif candidate_body.startswith("-"):
                old_actual += 1
            elif candidate_body.startswith("+"):
                new_actual += 1
            body_end += 1

        rendered_old_count = "" if old_count is None and old_actual == 1 else f",{old_actual}"
        rendered_new_count = "" if new_count is None and new_actual == 1 else f",{new_actual}"
        rendered_header = (
            f"@@ -{old_start}{rendered_old_count} +{new_start}{rendered_new_count} @@"
            f"{section}{ending}"
        )
        if rendered_header != line:
            changed = True
        normalized.append(rendered_header)
        normalized.extend(lines[index + 1:body_end])
        index = body_end

    return "".join(normalized) if changed else unified_diff


_CANONICAL_REPAIR_DIFF_CONTRACT = (
    "CRITICAL PATCH FORMAT CONTRACT\n"
    "- proposed_diff MUST contain raw Git unified diff text.\n"
    "- The first non-whitespace content MUST be: diff --git\n"
    "- Every modified file MUST use:\n"
    "  diff --git a/<relative-path> b/<relative-path>\n"
    "  --- a/<relative-path>\n"
    "  +++ b/<relative-path>\n"
    "  @@ -oldStart,oldCount +newStart,newCount @@\n"
    "- Repository-relative paths only; absolute paths and traversal are forbidden.\n"
    "- The Codex/apply_patch dialect is invalid for AMF-252.\n"
    "- Markdown fences, prose, JSON, commands, secrets, and test disabling are forbidden in proposed_diff.\n"
    "- Forbidden markers include: *** Begin Patch, *** Update File:, *** Add File:, *** Delete File:, *** End Patch.\n"
    "- If no safe Git unified diff can be produced, return proposed_diff = \"\" and no_fix_reason.\n"
    "- Every hunk MUST contain at least one unchanged context line prefixed by a single space.\n"
    "- Prefer 3 context lines where source size allows it.\n"
    "- Never emit zero-context hunks.\n"
    "- Hunk header old/new counts MUST match the actual hunk body lines.\n"
    "- Removed/replaced old code MUST use \"-\" prefix.\n"
    "- Added/replacement new code MUST use \"+\" prefix.\n"
    "- Unchanged context MUST use a single leading space.\n"
    "- Never represent old code being replaced as unchanged context.\n"
    "- Never add replacement code without deleting the replaced code when replacement is intended.\n"
    "- Do not fabricate line numbers or file contents.\n\n"
    "VALID:\n"
    "diff --git a/src/main/java/com/example/Foo.java b/src/main/java/com/example/Foo.java\n"
    "--- a/src/main/java/com/example/Foo.java\n"
    "+++ b/src/main/java/com/example/Foo.java\n"
    "@@ -10,1 +10,1 @@\n"
    "-    final Sort sort = new Sort(direction, column);\n"
    "+    final Sort sort = Sort.by(direction, column);\n\n"
    "INVALID:\n"
    "*** Begin Patch\n"
    "*** Update File: src/main/java/com/example/Foo.java\n"
    "@@\n"
    "- old\n"
    "+ new\n"
    "*** End Patch\n\n"
)


from migration_factory.repair_loop.patch_gate import extract_touched_paths


def _safe_diagnostic_text(value: Any, *, limit: int = 1000) -> str:
    text = redact_model_summary(str(value or ""))
    text = _DIAGNOSTIC_SECRET_VALUE_RE.sub(r"\1[redacted-secret]", text)
    return text[:limit]


# ── F5-T3: Deterministic repair artifact ─────────────────────────────


class RepairArtifactPhase:
    REPAIR = "repair"


def _build_deterministic_repair_payload(
    *,
    failure_evidence: FailureEvidence,
    context_pack: RepairContextPack,
    source_profile: str = "",
    target_profile: str = "",
    repair_intelligence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": "2.0.0",
        "phase": RepairArtifactPhase.REPAIR,
        "job_id": context_pack.job_id,
        "stage_index": context_pack.stage_index,
        "failure_source": failure_evidence.failure_source.value,
        "failure_summary": failure_evidence.failure_summary,
        "normalized_compiler_errors": [
            e.message for e in failure_evidence.compiler_errors
        ],
        "normalized_test_failures": [
            t.message for t in failure_evidence.test_failures
        ],
        "changed_files": list(failure_evidence.changed_files),
        "source_profile": source_profile or failure_evidence.source_profile,
        "target_profile": target_profile or failure_evidence.target_profile,
        "context_pack_checksum": context_pack.context_pack_checksum,
        "failure_evidence_checksum": failure_evidence.content_checksum,
        "base_repo_state_checksum": context_pack.base_repo_state_checksum,
        "accepted_artifact_checksums": list(failure_evidence.accepted_artifact_checksums),
        "diagnostic_metadata": dict(sorted(failure_evidence.diagnostic_metadata.items())),
        "allowed_repair_mode_hints": ["source_patch", "dependency_patch", "config_patch"],
        "created_at": utc_now_text(),
    }
    if repair_intelligence:
        payload["repair_intelligence"] = repair_intelligence
    return payload


# ── F5-T4/T5: Primary/Reviewer repair contracts ─────────────────────


def _source_context_value(source_context: Any, key: str, default: Any = "") -> Any:
    if isinstance(source_context, dict):
        return source_context.get(key, default)
    return getattr(source_context, key, default)


def _format_authoritative_source_contexts(source_contexts: list[Any]) -> str:
    """Render bounded current source consistently in repair prompts."""
    parts = []
    for source_context in source_contexts:
        get = functools.partial(_source_context_value, source_context)
        path = get("path")
        parts.append(
            f"FILE_PATH: {path}\n"
            f"SOURCE_FILE_SHA256: {get('source_file_sha256') or get('content_checksum')}\n"
            f"CONTEXT_EXCERPT_SHA256: {get('context_excerpt_sha256')}\n"
            f"CONTEXT_IS_COMPLETE: {bool(get('context_is_complete', False))}\n"
            f"SOURCE_LINES: {get('start_line') or get('line_start')}-{get('end_line') or get('line_end')}\n"
            f"CURRENT_AUTHORITATIVE_SOURCE:\n{get('content')}\n"
            f"END_CURRENT_AUTHORITATIVE_SOURCE: {path}"
        )
    return "\n\n".join(parts)


_STRUCTURED_REPAIR_EDIT_CONTRACT = (
    "STRUCTURED EDIT CONTRACT (required when bounded authoritative source context is present):\n"
    "- Source excerpts may be incomplete. Never infer file termination or add synthetic closing braces from an excerpt.\n"
        "- When CONTEXT_IS_COMPLETE is false, proposed_edits is mandatory for any touched file; do not emit a whole-file replacement hunk; raw unified-diff fallback is NOT permitted for that incomplete file.\n"
        "- proposed_diff is permitted only for touched files whose authoritative source context is complete, or for files not governed by an incomplete source context.\n"
    "- Each edit MUST contain path, expected_source_sha256, exact_old_text, and exact_new_text.\n"
    "- Use the exact per-file SOURCE_FILE_SHA256 for expected_source_sha256; never reuse a SHA across files or use a context-pack checksum.\n"
    "- exact_old_text MUST be copied verbatim from CURRENT_AUTHORITATIVE_SOURCE and must occur exactly once in that current file.\n"
    "- Use the smallest exact replacement possible: preserve unrelated code and do not reconstruct whole methods or classes.\n"
    "- Use multiple independent, non-overlapping edits instead of one large replacement spanning unrelated changes.\n"
    "- Do not normalize indentation, EOL style, trailing whitespace, or any other whitespace.\n"
    "- Leave proposed_diff empty when proposed_edits is supplied; the application layer will generate the final diff.\n"
)


def _pom_prompt_context(context_pack: RepairContextPack, intelligence: dict[str, Any]) -> str:
    pom_path = str((intelligence.get("pom") or {}).get("path") or "")
    for context in context_pack.source_contexts:
        if str(getattr(context, "path", "")).replace("\\", "/") == pom_path:
            return (
                f"AUTHORITATIVE_POM_PATH: {pom_path}\n"
                f"AUTHORITATIVE_POM_SHA256: {getattr(context, 'source_file_sha256', '') or getattr(context, 'content_checksum', '')}\n"
                f"AUTHORITATIVE_POM_XML:\n{context.content}\nEND_AUTHORITATIVE_POM_XML"
            )
    return ""


def _primary_repair_prompt(
    context_pack: RepairContextPack, deterministic_checksum: str,
    authoritative_facts: dict[str, Any], failure_evidence: FailureEvidence,
) -> str:
    if authoritative_facts.get("eligibility") is True:
        return (
            "You are the AMF-252 POM Repair V1 proposer. Return ONLY valid JSON.\n"
            "Produce the smallest justified edit to the exact authoritative pom.xml.\n"
            "Required keys: root_cause, fix_strategy, changed_files, proposed_diff, proposed_edits, confidence, rationale.\n"
            "Use the existing structured exact-edit contract below.\n\n"
            "HARD V1 RULES:\n"
            "- Modify only the exact authoritative pom.xml path.\n"
            "- Dependency removal, Java/source/test/config changes are forbidden.\n"
            "- Never invent a version; an exact replacement must be in metadata_lookup.available_versions.\n"
            "- UNKNOWN is not NOT_FOUND.\n"
            "- Account for every shared-property consumer; do not claim compatibility is proven.\n"
            "- Prefer the smallest justified POM blast radius.\n\n"
            f"DETERMINISTIC_REPAIR_ARTIFACT_CHECKSUM: {deterministic_checksum}\n"
            f"EXACT_FAILURE_EVIDENCE:\n{json.dumps(failure_evidence_to_dict(failure_evidence), sort_keys=True)}\n\n"
            f"COMPACT_POM_REPAIR_V1_INTELLIGENCE:\n{json.dumps(authoritative_facts, sort_keys=True)}\n\n"
            f"{_STRUCTURED_REPAIR_EDIT_CONTRACT}\n"
            f"{_pom_prompt_context(context_pack, authoritative_facts)}\n\n"
            f"USER_REVISION_INSTRUCTION:\n{context_pack.user_comments}\n\n"
            f"PRIOR_REVIEWER_NOTES:\n{context_pack.prior_reviewer_notes}"
        )
    context_dict = context_pack_to_dict(context_pack)
    source_contexts = context_dict.get("source_contexts") or []
    source_section = (
        "\n\nSOURCE CONTEXT:\n" + _format_authoritative_source_contexts(source_contexts)
        if source_contexts else ""
    )
    retry_contract = _post_apply_retry_contract(context_pack)
    return (
        "You are the AMF-252 repair proposer.\n"
        "Your task is to produce a minimal, safe repair that fixes the failing build/test evidence. "
        "Use structured exact edits (proposed_edits) when bounded source context is present; "
        "fall back to a raw Git unified diff (proposed_diff) only for complete-file contexts.\n\n"
        "Return ONLY valid JSON. Do NOT wrap in Markdown fences or code blocks. "
        "Do NOT include any text before or after the JSON.\n\n"
        "Required JSON keys: "
        "root_cause (string), fix_strategy (string), changed_files (list of file paths), "
        "proposed_diff (legacy unified diff string), proposed_edits (optional bounded edit list), "
        "confidence (0.0-1.0), rationale (string). "
        "deterministic_rule_id and risk are optional metadata and must never block diff generation.\n"
        "Only set no_fix_reason when the provided context lacks enough evidence to safely create a patch.\n"
        "If no_fix_reason is set, explain exactly which required evidence is missing.\n\n"
        + _CANONICAL_REPAIR_DIFF_CONTRACT
        + _STRUCTURED_REPAIR_EDIT_CONTRACT
        + "CONSTRAINTS:\n"
        "- Do NOT include commands, paths to execute, provider data, endpoint data, "
        "env data, deployment data, or approvals.\n"
        "- Do NOT include absolute Windows paths.\n"
        "- Do NOT include absolute POSIX host paths.\n"
        "- Do NOT include markdown code fences in proposed_diff.\n"
        "- Do NOT include explanatory prose inside proposed_diff.\n"
        "- Do NOT include plain source code without diff headers.\n"
        "- Do NOT include JSON embedded inside proposed_diff.\n"
        "- In normal repair mode, either proposed_edits or proposed_diff must be non-empty.\n"
        "- Never treat the last supplied excerpt line as EOF.\n"
        "- Empty proposed_diff is valid only when proposed_edits is supplied; an empty repair is unavailable.\n"
        "- Do not skip or disable tests as a fix.\n"
        "- The fix must stay within the sandbox scope and declared changed files.\n\n"
        f"Deterministic repair artifact checksum: {deterministic_checksum}\n"
        f"{retry_contract}"
        f"Source context is provided below with exact file contents bounded around error locations.\n"
        f"Use this source context to produce an exact applicable unified diff.\n"
        f"{source_section}\n\n"
        f"Context:\n{json.dumps(context_dict, sort_keys=True)}"
    )


def _reviewer_repair_prompt(
    primary_output: dict[str, Any],
    failure_evidence: FailureEvidence,
    context_pack: RepairContextPack,
    deterministic_checksum: str,
    context_checksum: str,
    primary_checksum: str,
    diff_checksum: str,
    authoritative_facts: dict[str, Any],
) -> str:
    source_contexts = list(context_pack.source_contexts or [])
    retry_contract = _post_apply_retry_contract(context_pack)
    if authoritative_facts.get("eligibility") is True:
        return (
            "You are the AMF-252 POM Repair V1 reviewer. Return ONLY valid JSON.\n"
            "Independently verify the proposer against the exact failure, exact POM, and compact intelligence.\n"
            "Reject non-POM changes, dependency removal, invented versions, UNKNOWN->NOT_FOUND, unsupported shared-property changes, and unsupported compatibility claims.\n"
            "Use the existing structured exact-edit contract; final edits may touch only the exact POM path.\n\n"
            f"DETERMINISTIC_REPAIR_ARTIFACT_CHECKSUM: {deterministic_checksum}\n"
            f"CONTEXT_PACK_CHECKSUM: {context_checksum}\n"
            f"PRIMARY_OUTPUT_CHECKSUM: {primary_checksum}\n"
            f"DIFF_CHECKSUM: {diff_checksum}\n"
            f"EXACT_FAILURE_EVIDENCE:\n{json.dumps(failure_evidence_to_dict(failure_evidence), sort_keys=True)}\n\n"
            f"COMPACT_POM_REPAIR_V1_INTELLIGENCE:\n{json.dumps(authoritative_facts, sort_keys=True)}\n\n"
            f"{_pom_prompt_context(context_pack, authoritative_facts)}\n\n"
            f"PROPOSER_OUTPUT:\n{json.dumps(primary_output, sort_keys=True)}\n\n"
            "Return keys: proposed_diff, proposed_edits, changed_files, review_notes, confidence, decision, "
            "reviewed_context_checksum, reviewed_primary_output_checksum, reviewed_diff_checksum.\n\n"
            f"USER_REVISION_INSTRUCTION:\n{context_pack.user_comments}\n\n"
            f"PRIOR_REVIEWER_NOTES:\n{context_pack.prior_reviewer_notes}"
        )
    return (
        "You are the AMF-252 final repair author and reviewer. Inspect exact failure "
        "evidence, bounded source context, proposer reasoning, and proposer diff. "
        "Correct or replace proposer work and return the best final raw Git unified diff.\n\n"
        "Return JSON with keys:\n"
        "  proposed_diff (legacy raw unified diff; empty when proposed_edits is supplied), "
        "proposed_edits (preferred bounded edit list with path, expected_source_sha256, exact_old_text, exact_new_text), "
        "changed_files (list), review_notes (list), confidence (0.0-1.0), "
        "optional decision/risks/policy_concerns, "
        "reviewed_context_checksum, reviewed_primary_output_checksum, "
        "reviewed_diff_checksum.\n\n"
        + _CANONICAL_REPAIR_DIFF_CONTRACT
        + _STRUCTURED_REPAIR_EDIT_CONTRACT
        + "- Independently verify every proposed edit against the supplied current authoritative source.\n"
        + "- Improve/correct proposer diff when evidence shows it is incomplete or wrong.\n"
        "- Use repository-relative paths only; reject commands, secrets, test disabling, "
        "absolute paths, traversal, or deployment/environment changes.\n"
        "- Bind your decision to the exact checksums provided.\n\n"
        f"DETERMINISTIC_REPAIR_ARTIFACT_CHECKSUM: {deterministic_checksum}\n"
        f"CONTEXT_PACK_CHECKSUM: {context_checksum}\n"
        f"PRIMARY_OUTPUT_CHECKSUM: {primary_checksum}\n"
        f"DIFF_CHECKSUM: {diff_checksum}\n"
        f"{retry_contract}"
        f"FAILURE EVIDENCE:\n{json.dumps(failure_evidence_to_dict(failure_evidence), sort_keys=True)}\n\n"
        f"SOURCE/CONTEXT PACK:\n{json.dumps(context_pack_to_dict(context_pack), sort_keys=True)}\n\n"
        f"CURRENT AUTHORITATIVE SOURCE BLOCKS:\n{_format_authoritative_source_contexts(source_contexts)}\n\n"
        f"PROPOSER OUTPUT:\n{json.dumps(primary_output, sort_keys=True)}"
    )


def _coerce_primary_repair_output(content: str) -> dict[str, Any]:
    content = str(content)
    if not content.strip():
        raise RepairReviewChainProductionError(
            "invalid_response_missing_content: primary repair output is empty"
        )
    try:
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise RepairReviewChainProductionError(
                f"invalid_response_non_json: parsed value is {type(parsed).__name__}, expected dict"
            )
    except json.JSONDecodeError as exc:
        snippet = content[:1000]
        raise RepairReviewChainProductionError(
            f"invalid_response_non_json: JSON parse error — {exc.msg} "
            f"(line {exc.lno}, col {exc.colpos}). "
            f"Content length={len(content)}, first 1000 chars: {snippet}"
        )

    required = {"root_cause", "fix_strategy", "changed_files", "proposed_diff", "confidence", "rationale"}
    missing = required - set(parsed.keys())
    if missing:
        raise RepairReviewChainProductionError(
            f"invalid_response_schema_validation_failed: missing required fields: {sorted(missing)}"
        )

    proposed_diff = str(parsed.get("proposed_diff") or "")
    edits = _structured_edits(parsed.get("proposed_edits"))
    no_fix_reason = str(parsed.get("no_fix_reason") or "")
    if not proposed_diff.strip() and not edits:
        if no_fix_reason.strip():
            return parsed
        raise RepairReviewChainProductionError(
            "invalid_response_missing_repair: proposed_diff and proposed_edits are both empty"
        )
    if "```" in proposed_diff:
        raise RepairReviewChainProductionError(
            "invalid_response_markdown_fenced_diff: proposed_diff is wrapped in Markdown fences"
        )
    if proposed_diff.strip() and not _looks_like_unified_diff(proposed_diff):
        raise RepairReviewChainProductionError(
            "invalid_response_non_unified_diff: proposed_diff does not contain unified diff markers"
        )

    return parsed


def _coerce_reviewer_repair_output(
    content: str,
    deterministic_checksum: str,
    context_checksum: str,
    primary_checksum: str,
    diff_checksum: str,
) -> dict[str, Any]:
    content = str(content)
    if not content.strip():
        raise RepairReviewChainProductionError(
            "invalid_response_missing_content: reviewer output is empty"
        )
    try:
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise RepairReviewChainProductionError(
                f"invalid_response_non_json: reviewer parsed value is {type(parsed).__name__}, expected dict"
            )
    except json.JSONDecodeError as exc:
        snippet = content[:1000]
        raise RepairReviewChainProductionError(
            f"invalid_response_non_json: reviewer JSON parse error — {exc.msg} "
            f"(line {exc.lno}, col {exc.colpos}). "
            f"Content length={len(content)}, first 1000 chars: {snippet}"
        )

    proposed_diff = str(parsed.get("proposed_diff") or "")
    edits = _structured_edits(parsed.get("proposed_edits"))
    decision = str(parsed.get("decision") or "").strip().lower()
    if decision not in {"", "accept", "revise", "reject"}:
        raise RepairReviewChainProductionError(f"invalid reviewer decision {decision!r}")
    if decision == "revise":
        decision = "request_revision"

    return {
        "decision": decision,
        "proposed_diff": proposed_diff,
        "proposed_edits": edits,
        "changed_files": parsed.get("changed_files") if isinstance(parsed.get("changed_files"), list) else [],
        "notes": parsed.get("review_notes") if isinstance(parsed.get("review_notes"), list) else parsed.get("notes") if isinstance(parsed.get("notes"), list) else [],
        "confidence": float(parsed.get("confidence", 0.8)),
        "risks": parsed.get("risks") if isinstance(parsed.get("risks"), list) else [],
        "policy_concerns": parsed.get("policy_concerns") if isinstance(parsed.get("policy_concerns"), list) else [],
        # These are model claims, not server defaults. Missing or incorrect
        # claims must fail closed in the caller's checksum comparison.
        "reviewed_context_checksum": str(parsed.get("reviewed_context_checksum") or ""),
        "reviewed_primary_output_checksum": str(parsed.get("reviewed_primary_output_checksum") or ""),
        "reviewed_diff_checksum": str(parsed.get("reviewed_diff_checksum") or ""),
        "review_dimensions": parsed.get("review_dimensions") if isinstance(parsed.get("review_dimensions"), dict) else {},
    }


def _compute_primary_repair_checksum(output: dict[str, Any]) -> str:
    payload = {
        "root_cause": str(output.get("root_cause", "")),
        "fix_strategy": str(output.get("fix_strategy", "")),
        "changed_files": list(output.get("changed_files", [])),
        "proposed_diff": str(output.get("proposed_diff", "")),
        "proposed_edits": _structured_edits(output.get("proposed_edits")),
        "deterministic_rule_id": str(output.get("deterministic_rule_id", "")),
        "risk": str(output.get("risk", "")),
        "confidence": float(output.get("confidence", 0.0)),
        "rationale": str(output.get("rationale", "")),
        "no_fix_reason": str(output.get("no_fix_reason", "")),
    }
    return sha256_canonical_json(payload)


def _compute_reviewer_repair_checksum(output: dict[str, Any]) -> str:
    payload = {
        "decision": str(output.get("decision", "")),
        "proposed_diff": str(output.get("proposed_diff", "")),
        "proposed_edits": _structured_edits(output.get("proposed_edits")),
        "changed_files": list(output.get("changed_files", [])),
        "notes": list(output.get("notes", [])),
        "confidence": float(output.get("confidence", 0.0)),
        "risks": list(output.get("risks", [])),
        "policy_concerns": list(output.get("policy_concerns", [])),
        "reviewed_context_checksum": str(output.get("reviewed_context_checksum", "")),
        "reviewed_primary_output_checksum": str(output.get("reviewed_primary_output_checksum", "")),
        "reviewed_diff_checksum": str(output.get("reviewed_diff_checksum", "")),
    }
    return sha256_canonical_json(payload)


def _validate_primary_repair_output(output: dict[str, Any]) -> list[str]:
    failures: list[str] = []

    for key in ("root_cause", "fix_strategy"):
        if not isinstance(output.get(key), str) or not output[key].strip():
            failures.append(f"empty or missing required field {key!r}")

    changed = output.get("changed_files")
    if not isinstance(changed, list) or not all(isinstance(f, str) for f in changed):
        failures.append("changed_files must be a list of strings")

    confidence = output.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0.0 <= float(confidence) <= 1.0):
        failures.append("confidence must be a float between 0.0 and 1.0")

    diff = str(output.get("proposed_diff", ""))
    edits = _structured_edits(output.get("proposed_edits"))
    no_fix_reason = str(output.get("no_fix_reason") or "")
    if not diff.strip() and not edits:
        if not no_fix_reason.strip():
            failures.append("one of proposed_diff or proposed_edits must be non-empty")
    if diff.strip():
        if "```" in diff:
            failures.append("proposed_diff appears to be Markdown fenced")
        elif not _looks_like_unified_diff(diff):
            failures.append("proposed_diff does not appear to be a valid unified diff")

    if output.get("proposed_edits") is not None and not isinstance(output.get("proposed_edits"), list):
        failures.append("proposed_edits must be a list when supplied")
    for edit in edits:
        for key in ("path", "expected_source_sha256", "exact_old_text", "exact_new_text"):
            if not edit.get(key) and key != "exact_new_text":
                failures.append(f"structured edit is missing {key!r}")

    forbidden_paths = _check_forbidden_paths_in_diff(diff)
    if forbidden_paths:
        failures.extend(forbidden_paths)

    forbidden_fields = _check_forbidden_keys(output)
    if forbidden_fields:
        failures.extend(forbidden_fields)

    return failures


def _validate_candidate_diff(
    *,
    proposed_diff: str,
    changed_files: list[Any],
    role: str,
) -> list[str]:
    """Validate model diff shape/scope without semantic policy gating."""
    failures: list[str] = []
    if not proposed_diff.strip():
        failures.append(f"{role} proposed_diff is empty")
    elif any(marker in proposed_diff for marker in (
        "*** Begin Patch",
        "*** Update File:",
        "*** Add File:",
        "*** Delete File:",
        "*** End Patch",
    )):
        failures.append(f"{role} proposed_diff uses the unsupported apply_patch dialect")
    elif "```" in proposed_diff:
        failures.append(f"{role} proposed_diff is Markdown fenced")
    elif not _looks_like_unified_diff(proposed_diff):
        failures.append(f"{role} proposed_diff is not a usable unified diff")
    if not isinstance(changed_files, list) or not all(isinstance(item, str) for item in changed_files):
        failures.append(f"{role} changed_files must be a list of strings")
    else:
        touched_paths, path_errors = extract_touched_paths(proposed_diff)
        if path_errors:
            failures.extend(f"{role} {error}" for error in path_errors)
        declared = {str(path).replace("\\", "/").removeprefix("a/").removeprefix("b/") for path in changed_files}
        actual = {str(path).replace("\\", "/") for path in touched_paths}
        if declared != actual:
            failures.append(f"{role} changed_files do not exactly match diff paths")
    failures.extend(_check_forbidden_paths_in_diff(proposed_diff))
    return failures


def _validate_model_candidate(
    *,
    output: dict[str, Any],
    role: str,
    context_pack: RepairContextPack,
    sandbox_path: str | Path | None,
    output_dir: Path,
) -> tuple[str, list[str], list[str]]:
    """Return canonical diff, validation failures, and touched paths."""
    edits = _structured_edits(output.get("proposed_edits"))
    structured_paths = {str(edit.get("path") or "").replace("\\", "/") for edit in edits}
    no_fix_reason = str(output.get("no_fix_reason") or "")
    if not edits and no_fix_reason.strip():
        proposed_diff_check = str(output.get("proposed_diff") or "")
        if not proposed_diff_check.strip():
            return "", [], []
    if edits:
        if sandbox_path is None:
            return "", [f"{role} structured edits require sandbox_path for canonicalization"], []
        canonical, edit_failures = _apply_structured_edits_to_shadow(
            edits=edits,
            sandbox_path=sandbox_path,
            output_dir=output_dir,
            context_pack=context_pack,
        )
        if edit_failures:
            return "", [f"{role} {failure}" for failure in edit_failures], []
        proposed_diff = canonical
    else:
        proposed_diff = str(output.get("proposed_diff") or "")
    normalized = _normalize_unified_diff_hunk_counts(proposed_diff)
    failures = _candidate_source_validation(
        proposed_diff=normalized,
        changed_files=list(output.get("changed_files") or []),
        context_pack=context_pack,
        sandbox_path=sandbox_path,
        allow_bounded_paths=structured_paths,
    )
    if not failures:
        failures.extend(_strict_git_applicability(
            proposed_diff=normalized,
            sandbox_path=sandbox_path,
            output_dir=output_dir,
        ))
    sections, parse_failures = _strict_parse_unified_diff(normalized)
    if parse_failures:
        failures.extend(parse_failures)
    touched_paths = [str(section["path"]) for section in sections]
    return normalized, failures, touched_paths


def _post_apply_retry_contract(context_pack: RepairContextPack) -> str:
    if int(context_pack.cycle_number or 0) < 2:
        return ""
    current_checksums = "\n".join(
        f"- {context.path}: {context.content_checksum}"
        for context in context_pack.source_contexts
    )
    return (
        "\nPOST-APPLY RETRY CONTRACT:\n"
        "PRIOR PATCH IS ALREADY APPLIED.\n"
        "The supplied source is the current authoritative sandbox state.\n"
        "Do not propose edits against the pre-patch source.\n"
        "Every exact_old_text must occur exactly once in the supplied current source.\n"
        "expected_source_sha256 must correspond to that same current source.\n"
        f"Current source checksums:\n{current_checksums}\n"
    )


def _candidate_correction_prompt(
    *,
    context_pack: RepairContextPack,
    candidate_diff: str,
    failures: list[str],
    context_checksum: str,
    primary_checksum: str,
    diff_checksum: str,
    authoritative_facts: dict[str, Any],
) -> str:
    retry_contract = _post_apply_retry_contract(context_pack)
    source_excerpt = _format_authoritative_source_contexts(
        list(context_pack.source_contexts or [])
    )
    diagnostics = "\n".join(f"- {_safe_diagnostic_text(failure, limit=1200)}" for failure in failures)[:6000]
    candidate_stripped = candidate_diff.strip()
    if not candidate_stripped:
        correction_guidance = (
            "CORRECTION GUIDANCE:\n"
            "Both upstream candidates (proposer and reviewer) were technically rejected.\n"
            "Do not attempt to repair an empty diff textually.\n"
            "Use the supplied CURRENT_AUTHORITATIVE_SOURCE contexts below.\n"
            "For touched files with CONTEXT_IS_COMPLETE=false: structured edits are required; no raw-diff fallback for that incomplete file. Complete touched files may preserve existing raw-diff compatibility.\n"
            "Use minimal exact replacements from current source only.\n"
            "Use the exact supplied per-file SOURCE_FILE_SHA256 values as expected_source_sha256.\n"
            "expected_source_sha256 MUST equal the supplied SOURCE_FILE_SHA256 for that exact path.\n"
            "exact_old_text MUST be copied verbatim from CURRENT_AUTHORITATIVE_SOURCE.\n"
            "exact_old_text MUST identify exactly one current-source occurrence.\n"
            "Never reuse one file's SHA for another file.\n"
            "Do not use context_pack_checksum as a file checksum.\n"
            "Leave proposed_diff empty when proposed_edits is supplied.\n\n"
        )
    else:
        correction_guidance = ""
    v1_challenge = (
        "REVIEWER CHALLENGE RULES:\n"
        "- Reject invented versions, UNKNOWN→NOT_FOUND, unsupported dependency removal, broad shared-property edits, and unsupported compatibility assumptions.\n"
        f"AUTHORITATIVE FACTS:\n{json.dumps(authoritative_facts, sort_keys=True)}\n"
        if authoritative_facts.get("eligibility") is True else ""
    )
    return (
        "You are correcting one failed AMF-252 repair candidate. Return only JSON matching "
        "RepairReviewerOutput.\n\n"
        + _STRUCTURED_REPAIR_EDIT_CONTRACT
        + "- For touched files with CONTEXT_IS_COMPLETE=false: structured edits are required; no raw-diff fallback for that incomplete file. Complete touched files may preserve existing raw-diff compatibility.\n"
        "- path MUST identify exactly one supplied source file.\n"
        "- expected_source_sha256 MUST equal the SOURCE_FILE_SHA256 for that exact file.\n"
        "- Never reuse one file's SHA for another file.\n"
        "- Do not use context_pack_checksum as a file checksum.\n"
        "- exact_old_text MUST be copied verbatim from CURRENT_AUTHORITATIVE_SOURCE.\n"
        "- exact_old_text MUST identify exactly one current-source occurrence.\n"
        "- Leave proposed_diff empty when proposed_edits is supplied.\n"
        "- Return minimal exact replacements only: no reconstructed whole methods, indentation/EOL/whitespace normalization, or overlapping edits.\n"
        "- If proposed_diff is used as the fallback, every hunk MUST be contextual with at least one unchanged "
        "space-prefixed line (prefer three where possible), never zero-context, with correct old/new counts "
        "and exact '-'/'+'/' ' prefixes.\n\n"
        "Do not return prose or Markdown fences. Do not use apply_patch markers. Do not invent files, "
        "change unrelated code, invent anchors/source, or guess hunk line numbers. Preserve unrelated code.\n\n"
        f"Context pack checksum: {context_checksum}\n"
        f"Primary output checksum: {primary_checksum}\n"
        f"Proposer diff checksum: {diff_checksum}\n\n"
        f"USER_REVISION_INSTRUCTION:\n{context_pack.user_comments}\n\n"
        f"PRIOR_REVIEWER_NOTES:\n{context_pack.prior_reviewer_notes}\n\n"
        f"{retry_contract}"
        f"{correction_guidance}"
        f"Deterministic validation failures:\n{diagnostics}\n\n"
        f"Candidate diff (bounded):\n{candidate_diff[:12000]}\n\n"
        f"Authoritative source context (bounded):\n{source_excerpt}\n\n"
        f"{v1_challenge}"
    )


def _unusable_primary_output(
    reason: str,
    raw_content: str = "",
    preserved_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shape proposer failure as reviewer input without treating it as a final diff.

    When preserved_fields contains safely parseable semantic values
    (root_cause, fix_strategy, rationale, no_fix_reason, confidence,
    abstention_reason, raw_output_ref, etc.), they are used instead of
    generic fallback text. Only fields that are truly missing fall back
    to the generic ''unavailable/unusable'' text.
    """
    preserved = preserved_fields or {}

    root_cause = preserved.get("root_cause")
    if not root_cause or not isinstance(root_cause, str) or not root_cause.strip():
        root_cause = "Proposer output unavailable or unusable."

    fix_strategy = preserved.get("fix_strategy")
    if not fix_strategy or not isinstance(fix_strategy, str) or not fix_strategy.strip():
        fix_strategy = "Reviewer must independently author a repair if evidence permits."

    rationale = preserved.get("rationale")
    no_fix_reason = preserved.get("no_fix_reason")
    abstention_reason = preserved.get("abstention_reason")
    raw_output_ref = preserved.get("raw_output_ref")
    confidence = preserved.get("confidence")

    return {
        "root_cause": root_cause,
        "fix_strategy": fix_strategy,
        "changed_files": list(preserved.get("changed_files", [])),
        "proposed_diff": str(preserved.get("proposed_diff", "")),
        "deterministic_rule_id": preserved.get("deterministic_rule_id"),
        "risk": preserved.get("risk"),
        "confidence": float(confidence) if confidence is not None else 0.0,
        "rationale": str(rationale)[:2000] if rationale else str(reason)[:2000],
        "no_fix_reason": str(no_fix_reason)[:2000] if no_fix_reason else str(reason)[:2000],
        "abstention_reason": str(abstention_reason)[:2000] if abstention_reason else str(reason)[:2000],
        "raw_output_ref": str(raw_output_ref)[:2000] if raw_output_ref else "",
        "proposer_raw_output_available": bool(raw_content),
    }


def _safe_abstention_primary_output(preserved_fields: dict[str, Any] | None = None) -> dict[str, Any]:
    no_fix = str((preserved_fields or {}).get("no_fix_reason", ""))
    return {
        "root_cause": str((preserved_fields or {}).get("root_cause", "")),
        "fix_strategy": str((preserved_fields or {}).get("fix_strategy", "")),
        "changed_files": list((preserved_fields or {}).get("changed_files", [])),
        "proposed_diff": str((preserved_fields or {}).get("proposed_diff", "")),
        "deterministic_rule_id": (preserved_fields or {}).get("deterministic_rule_id"),
        "risk": (preserved_fields or {}).get("risk"),
        "confidence": float((preserved_fields or {}).get("confidence", 0.0)),
        "rationale": str((preserved_fields or {}).get("rationale", "")),
        "no_fix_reason": no_fix,
        "abstention_reason": no_fix,
        "usability_reason": "MODEL_INSUFFICIENT_EVIDENCE_ABSTENTION",
        "raw_output_ref": str((preserved_fields or {}).get("raw_output_ref", "")),
        "proposer_raw_output_available": bool((preserved_fields or {}).get("raw_content", "")),
    }


def _looks_like_unified_diff(diff: str) -> bool:
    if not diff or not diff.strip():
        return False
    text = diff.strip()
    if "```" in text:
        return False
    has_file_header = text.startswith("diff --git ") and "\n--- " in text and "\n+++ " in text
    has_hunk = "@@" in text
    has_change = any(
        line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        for line in text.splitlines()
    )
    return has_file_header and has_hunk and has_change


def _structured_edits(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    edits: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        edits.append({
            "path": str(item.get("path") or ""),
            "expected_source_sha256": str(item.get("expected_source_sha256") or ""),
            "exact_old_text": str(item.get("exact_old_text") or ""),
            "exact_new_text": str(item.get("exact_new_text") or ""),
        })
    return edits


def _source_contexts_by_path(context_pack: RepairContextPack) -> dict[str, Any]:
    return {
        _relative_diff_path(str(context.path)): context
        for context in context_pack.source_contexts
    }


def _source_paths_from_context(context_pack: RepairContextPack) -> dict[str, str]:
    return {
        path: str(getattr(context, "source_file_sha256", "") or context.content_checksum)
        for path, context in _source_contexts_by_path(context_pack).items()
    }


def _safe_source_path(sandbox_path: str | Path, relative_path: str) -> Path | None:
    normalized = str(relative_path).replace("\\", "/")
    if _path_errors(normalized):
        return None
    sandbox = Path(sandbox_path).resolve()
    candidate = _normalize_and_check_path(normalized, sandbox, allow_absolute=False)
    if candidate is None or not candidate.is_file():
        return None
    return candidate


def _apply_structured_edits_to_shadow(
    *,
    edits: list[dict[str, str]],
    sandbox_path: str | Path,
    output_dir: Path,
    context_pack: RepairContextPack,
) -> tuple[str, list[str]]:
    """Generate a canonical diff from exact replacements without touching sandbox."""
    if not edits:
        return "", []
    context_checksums = _source_paths_from_context(context_pack)
    context_sources = _source_contexts_by_path(context_pack)
    files: dict[str, bytes] = {}
    replacements: dict[str, list[tuple[int, int, str]]] = {}
    failures: list[str] = []
    for edit in edits:
        path = str(edit.get("path") or "").replace("\\", "/")
        source_path = _safe_source_path(sandbox_path, path)
        if source_path is None:
            failures.append(f"structured edit path is not a safe existing file: {path}")
            continue
        source_bytes = source_path.read_bytes()
        source_checksum = hashlib.sha256(source_bytes).hexdigest()
        expected = str(edit.get("expected_source_sha256") or "")
        if expected != source_checksum:
            failures.append(f"source checksum mismatch for {path}: expected {expected}, live {source_checksum}")
        context_checksum = context_checksums.get(path)
        if context_pack.source_contexts and context_checksum is None:
            failures.append(f"source context is missing for {path}")
        elif context_checksum != source_checksum:
            failures.append(f"source context is stale for {path}: context {context_checksum}, live {source_checksum}")
        old_text = str(edit.get("exact_old_text") or "")
        new_text = str(edit.get("exact_new_text") or "")
        if not old_text:
            failures.append(f"structured edit has empty exact_old_text: {path}")
            continue
        context_source = context_sources.get(path)
        if context_source is None:
            failures.append(f"structured edit source context is missing for {path}")
            continue
        excerpt_sha256 = str(getattr(context_source, "context_excerpt_sha256", "") or "")
        if excerpt_sha256:
            actual_excerpt_sha256 = hashlib.sha256(str(context_source.content).encode("utf-8")).hexdigest()
            if excerpt_sha256 != actual_excerpt_sha256:
                failures.append(
                    f"source context excerpt checksum mismatch for {path}: "
                    f"context {excerpt_sha256}, content {actual_excerpt_sha256}"
                )
                continue
        # EOL-normalized preimage comparison (CRLF/LF-agnostic)
        _norm_old = _eol_normalize(old_text)
        _norm_context = _eol_normalize(str(context_source.content))
        context_matches = _norm_context.count(_norm_old)
        if context_matches != 1:
            failures.append(
                f"structured edit old text context match count for {path}: "
                f"{context_matches} (expected 1)"
            )
            continue
        try:
            source_text = source_bytes.decode("utf-8")
            old_bytes = old_text.encode("utf-8")
            new_text.encode("utf-8")
        except UnicodeDecodeError:
            failures.append(f"structured edit source is not UTF-8: {path}")
            continue
        # EOL-normalized source preimage count
        _norm_source = _eol_normalize(source_text)
        source_matches = _norm_source.count(_norm_old)
        if source_matches != 1:
            failures.append(f"structured edit old text match count for {path}: {source_matches} (expected 1)")
            continue
        # Convert old/new text to match source text line-ending convention
        _norm_new = _eol_normalize(new_text)
        if "\r\n" in source_text:
            _source_eol_old = _norm_old.replace("\n", "\r\n")
            _source_eol_new = _norm_new.replace("\n", "\r\n")
        elif "\r" in source_text:
            _source_eol_old = _norm_old.replace("\n", "\r")
            _source_eol_new = _norm_new.replace("\n", "\r")
        else:
            _source_eol_old = _norm_old
            _source_eol_new = _norm_new
        matches: list[int] = []
        offset = source_text.find(_source_eol_old)
        while offset >= 0:
            matches.append(offset)
            offset = source_text.find(_source_eol_old, offset + 1)
        if len(matches) != 1:
            failures.append(f"structured edit old text match count for {path}: {len(matches)} (expected 1)")
            continue
        start = matches[0]
        end = start + len(_source_eol_old)
        prior = replacements.setdefault(path, [])
        if any(start < existing_end and existing_start < end for existing_start, existing_end, _ in prior):
            failures.append(f"structured edits overlap for {path}")
        prior.append((start, end, _source_eol_new))
        files[path] = source_bytes

    if failures:
        return "", failures

    with tempfile.TemporaryDirectory(prefix="repair-diff-", dir=str(output_dir)) as temp_name:
        temp_root = Path(temp_name)
        old_root = temp_root / "old"
        new_root = temp_root / "new"
        generated: list[str] = []
        for path in sorted(files):
            old_bytes = files[path]
            source_text = old_bytes.decode("utf-8")
            new_text = source_text
            for start, end, replacement in sorted(replacements[path], reverse=True):
                # Offsets are text offsets and are applied from right to left,
                # so earlier offsets stay stable for Unicode source too.
                new_text = new_text[:start] + replacement + new_text[end:]
            new_bytes = new_text.encode("utf-8")
            old_file = old_root / PurePosixPath(path)
            new_file = new_root / PurePosixPath(path)
            old_file.parent.mkdir(parents=True, exist_ok=True)
            new_file.parent.mkdir(parents=True, exist_ok=True)
            old_file.write_bytes(old_bytes)
            new_file.write_bytes(new_bytes)
            result = subprocess.run(
                ["git", "diff", "--no-index", "--no-ext-diff", "--no-color", "--unified=3", "--", str(old_file), str(new_file)],
                cwd=str(temp_root),
                capture_output=True,
                text=False,
                check=False,
                timeout=60,
            )
            if result.returncode not in {0, 1}:
                failures.append(f"Git canonical diff generation failed for {path}: {(result.stderr or b'').decode('utf-8', 'replace')[-500:]}")
                continue
            if result.returncode == 0 or not result.stdout:
                failures.append(f"structured edit produced no change for {path}")
                continue
            raw = result.stdout.decode("utf-8", errors="strict")
            canonical_lines: list[str] = []
            header_seen = 0
            for line in raw.splitlines(keepends=True):
                body = _line_body(line)
                ending = line[len(body):]
                if header_seen == 0 and body.startswith("diff --git "):
                    canonical_lines.append(f"diff --git a/{path} b/{path}{ending}")
                    header_seen = 1
                elif header_seen == 1 and body.startswith("--- "):
                    canonical_lines.append(f"--- a/{path}{ending}")
                    header_seen = 2
                elif header_seen == 2 and body.startswith("+++ "):
                    canonical_lines.append(f"+++ b/{path}{ending}")
                    header_seen = 3
                else:
                    canonical_lines.append(line)
            generated.append("".join(canonical_lines))
        if failures:
            return "", failures
        return "".join(generated), []


def _candidate_source_validation(
    *,
    proposed_diff: str,
    changed_files: list[Any],
    context_pack: RepairContextPack,
    sandbox_path: str | Path | None,
    allow_bounded_paths: set[str] | None = None,
) -> list[str]:
    sections, failures = _strict_parse_unified_diff(proposed_diff)
    failures.extend(_check_forbidden_paths_in_diff(proposed_diff))
    if failures or not sections:
        return failures
    actual = {str(section["path"]).replace("\\", "/") for section in sections}
    declared = {str(path).replace("\\", "/").removeprefix("a/").removeprefix("b/") for path in changed_files}
    if actual != declared:
        failures.append(f"changed_files do not exactly match parsed paths: declared={sorted(declared)}, actual={sorted(actual)}")
    if sandbox_path is None:
        return failures

    context_checksums = _source_paths_from_context(context_pack)
    bounded_paths = {
        str(context.path).replace("\\", "/")
        for context in context_pack.source_contexts
        if not bool(getattr(context, "context_is_complete", False))
    }
    allowed_bounded_paths = {str(path).replace("\\", "/") for path in (allow_bounded_paths or set())}
    raw_bounded_paths = (actual & bounded_paths) - allowed_bounded_paths
    if raw_bounded_paths:
        failures.append(
            "raw unified diff is forbidden for incomplete source context; "
            f"use exact proposed_edits for: {sorted(raw_bounded_paths)}"
        )
    matched_ranges: dict[str, list[tuple[int, int]]] = {}
    for section in sections:
        path = str(section["path"])
        source_path = _safe_source_path(sandbox_path, path)
        if source_path is None:
            failures.append(f"candidate path is not a safe existing source file: {path}")
            continue
        source_bytes = source_path.read_bytes()
        source_checksum = hashlib.sha256(source_bytes).hexdigest()
        context_checksum = context_checksums.get(path)
        if context_checksums and context_checksum is None:
            failures.append(f"source context is missing for {path}")
        elif context_checksum != source_checksum:
            failures.append(f"source context checksum mismatch for {path}: context {context_checksum}, live {source_checksum}")
        try:
            source_lines = source_bytes.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            failures.append(f"candidate source is not UTF-8: {path}")
            continue
        ranges = matched_ranges.setdefault(path, [])
        for hunk in section["hunks"]:
            old_lines = [line[1:] for line in hunk["body"] if line.startswith((" ", "-"))]
            if not any(line.startswith(" ") for line in hunk["body"]):
                failures.append(
                    f"zero-context hunk rejected for {path} {hunk['header']}: "
                    "every changed hunk must contain unchanged context"
                )
            if not old_lines:
                insertion_index = max(0, min(len(source_lines), int(hunk["old_start"])))
                matches = [insertion_index]
            else:
                matches = [
                    index for index in range(0, len(source_lines) - len(old_lines) + 1)
                    if source_lines[index:index + len(old_lines)] == old_lines
                ]
            if len(matches) != 1:
                failures.append(
                    f"exact preimage match count for {path} {hunk['header']}: {len(matches)}; "
                    f"source checksum={source_checksum}"
                )
                continue
            start = matches[0]
            end = start + len(old_lines)
            expected_start = start if not old_lines else start + 1
            if int(hunk["old_start"]) != expected_start:
                failures.append(
                    f"hunk line anchor does not match unique preimage for {path} {hunk['header']}: "
                    f"header={hunk['old_start']}, expected={expected_start}"
                )
            if any(start < prior_end and prior_start < end for prior_start, prior_end in ranges):
                failures.append(f"overlapping hunk preimages for {path} {hunk['header']}")
            ranges.append((start, end))
            new_lines = [line[1:] for line in hunk["body"] if line.startswith((" ", "+"))]
            tail = source_lines[end:]
            max_overlap = min(len(new_lines), len(tail))
            overlap = 0
            for size in range(max_overlap, 1, -1):
                if new_lines[-size:] == tail[:size]:
                    overlap = size
                    break
            if overlap >= 2 and (overlap >= 3 or overlap * 2 >= max(1, len(new_lines))):
                failures.append(
                    f"duplicate-tail overlap for {path} {hunk['header']}: {overlap} inserted lines "
                    "duplicate the untouched source tail"
                )
    return failures


def _validate_pom_v1_candidate(
    *, proposed_diff: str, changed_files: list[Any], intelligence: dict[str, Any],
    context_pack: RepairContextPack, sandbox_path: str | Path | None,
) -> list[str]:
    """Enforce V1 semantic scope after the existing deterministic diff checks."""
    if intelligence.get("eligibility") is not True:
        return []
    pom_path = str((intelligence.get("pom") or {}).get("path") or "").replace("\\", "/")
    if set(str(path).replace("\\", "/") for path in changed_files) != {pom_path}:
        return ["POM Repair V1 candidate must modify only the authoritative pom.xml"]
    failures: list[str] = []
    removed = [line[1:] for line in proposed_diff.splitlines() if line.startswith("-") and not line.startswith("---")]
    available = set((intelligence.get("metadata_lookup") or {}).get("available_versions") or [])
    added = [line[1:] for line in proposed_diff.splitlines() if line.startswith("+") and not line.startswith("+++")]
    declaration = intelligence.get("declaration") or {}
    property_name = str(declaration.get("property_name") or "")
    allowed_tag = rf"<\s*{re.escape(property_name)}\b" if property_name else r"<\s*version\b"
    changed_lines = [line for line in (*removed, *added) if line.strip()]
    allowed_changed_line = (
        rf"^\s*<\s*{re.escape(property_name)}\b[^>]*>[^<]*</\s*{re.escape(property_name)}\s*>\s*$"
        if property_name else r"^\s*<\s*version\b[^>]*>[^<]*</\s*version\s*>\s*$"
    )
    if not changed_lines or any(
        not re.fullmatch(allowed_changed_line, line, re.I)
        or not re.search(allowed_tag, line, re.I)
        for line in changed_lines
    ):
        failures.append("POM Repair V1 permits changes only to the matched dependency version or its known property")
    if not added:
        failures.append("POM Repair V1 requires an added replacement version")
    for line in added:
        match = re.search(r"<version>\s*([^<{]+?)\s*</version>", line, re.I)
        if match and match.group(1).strip() not in available:
            failures.append("POM Repair V1 exact version is not in authoritative available_versions")
        if property_name and re.search(rf"<\s*{re.escape(property_name)}\b", line, re.I):
            value_match = re.search(r">\s*([^<{]+?)\s*</", line)
            if value_match and value_match.group(1).strip() not in available:
                failures.append("POM Repair V1 shared-property version is not in authoritative available_versions")
    pom_context = next(
        (
            context for context in context_pack.source_contexts
            if str(getattr(context, "path", "")).replace("\\", "/") == pom_path
        ),
        None,
    )
    original_text = str(getattr(pom_context, "content", "")) if pom_context is not None else ""
    if not original_text and sandbox_path is not None:
        source_path = _safe_source_path(sandbox_path, pom_path)
        if source_path is not None:
            original_text = source_path.read_text(encoding="utf-8", errors="strict")
    coordinate = intelligence.get("coordinate") or {}
    if not original_text or not coordinate:
        failures.append("POM Repair V1 cannot structurally verify authoritative POM preimage")
        return sorted(set(failures))
    original = inspect_pom_for_coordinate(
        original_text, coordinate.get("group_id", ""), coordinate.get("artifact_id", ""),
        coordinate.get("type", "jar"), coordinate.get("classifier", ""),
    )
    if not original or original.get("status") != "MATCH":
        failures.append("POM Repair V1 authoritative target declaration is not structurally verifiable")
        return sorted(set(failures))
    sections, parse_failures = _strict_parse_unified_diff(proposed_diff)
    if parse_failures or len(sections) != 1 or sections[0].get("path") != pom_path:
        failures.append("POM Repair V1 shadow candidate has invalid authoritative POM diff")
    else:
        shadow_lines = [_line_body(line) for line in original_text.splitlines(keepends=True)]
        for hunk in sections[0].get("hunks", []):
            old_lines = [line[1:] for line in hunk["body"] if line.startswith((" ", "-"))]
            new_lines = [line[1:] for line in hunk["body"] if line.startswith((" ", "+"))]
            start = int(hunk["old_start"]) - 1
            if start < 0 or shadow_lines[start:start + len(old_lines)] != old_lines:
                failures.append("POM Repair V1 shadow candidate preimage mismatch")
                break
            shadow_lines[start:start + len(old_lines)] = new_lines
        else:
            candidate = inspect_pom_for_coordinate(
                "\n".join(shadow_lines) + ("\n" if original_text.endswith(("\n", "\r")) else ""),
                coordinate.get("group_id", ""), coordinate.get("artifact_id", ""),
                coordinate.get("type", "jar"), coordinate.get("classifier", ""),
            )
            if not candidate or candidate.get("status") != "MATCH":
                failures.append("POM Repair V1 candidate removed or changed target dependency identity")
            else:
                identity_keys = ("group_id", "artifact_id", "type", "classifier", "declaration_kind", "property_name")
                if any(candidate.get(key) != original.get(key) for key in identity_keys):
                    failures.append("POM Repair V1 target dependency identity changed")
                old_consumers = {
                    (item.get("group_id"), item.get("artifact_id"), item.get("type", "jar"), item.get("classifier", ""), item.get("raw_version"), item.get("property_name"))
                    for item in original.get("known_property_consumers", [])
                }
                new_consumers = {
                    (item.get("group_id"), item.get("artifact_id"), item.get("type", "jar"), item.get("classifier", ""), item.get("raw_version"), item.get("property_name"))
                    for item in candidate.get("known_property_consumers", [])
                }
                if old_consumers != new_consumers:
                    failures.append("POM Repair V1 shared-property consumer set changed")
    return sorted(set(failures))


def _strict_git_applicability(
    *,
    proposed_diff: str,
    sandbox_path: str | Path | None,
    output_dir: Path,
) -> list[str]:
    if sandbox_path is None:
        return []
    patch_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".diff", prefix="candidate-", dir=str(output_dir), delete=False
        ) as handle:
            patch_path = Path(handle.name)
            patch_bytes = proposed_diff.encode("utf-8")
            if not patch_bytes.endswith((b"\n", b"\r")):
                patch_bytes += b"\n"
            handle.write(patch_bytes)
        result = subprocess.run(
            ["git", "apply", "--check", str(patch_path)],
            cwd=str(Path(sandbox_path).resolve()),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout or "git apply --check failed").strip()
            return [f"strict Git applicability failed: {detail[-1000:]}"]
        return []
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"strict Git applicability execution failed: {type(exc).__name__}"]
    finally:
        if patch_path is not None:
            patch_path.unlink(missing_ok=True)


def _check_forbidden_paths_in_diff(diff: str) -> list[str]:
    failures: list[str] = []
    forbidden_patterns = [
        "sandbox_path",
        ".git",
        ".env",
        "Dockerfile",
        "docker-compose",
        ".github/workflows",
        "deploy/",
        "deployment/",
        "k8s/",
        "helm/",
        ".migration",
    ]
    for pattern in forbidden_patterns:
        if pattern in diff:
            failures.append(f"diff contains forbidden path pattern {pattern!r}")
    return failures


def _check_forbidden_keys(data: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for key in (
        "sandbox_path", "argv", "env", "raw_command", "filesystem_target",
        "provider", "endpoint", "deployment", "env_ref", "user_supplied_file_path",
    ):
        if key in data and data[key]:
            failures.append(f"forbidden key {key!r} found in repair output")
    return failures


# ── F5-T6: Final reviewed repair diff artifact ─────────────────────


def _build_final_reviewed_repair_artifact(
    *,
    job_id: str,
    stage_index: int,
    failure_evidence: FailureEvidence,
    context_pack: RepairContextPack,
    primary_output: dict[str, Any],
    primary_checksum: str,
    reviewer_output: dict[str, Any],
    reviewer_checksum: str,
    deterministic_checksum: str,
    selected_diff: str,
    final_diff_source: str,
    selected_changed_files: list[str],
) -> dict[str, Any]:
    diff_checksum = sha256_canonical_json({"unified_diff": selected_diff})

    return {
        "schema_version": "2.0.0",
        "proposal_id": "",
        "job_id": job_id,
        "stage_index": stage_index,
        "failure_source": failure_evidence.failure_source.value,
        "failure_summary": failure_evidence.failure_summary,
        "deterministic_artifact_checksum": deterministic_checksum,
        "context_pack_checksum": context_pack.context_pack_checksum,
        "primary_output_checksum": primary_checksum,
        "reviewer_output_checksum": reviewer_checksum,
        "proposed_diff_checksum": diff_checksum,
        "changed_files": list(selected_changed_files),
        "base_repo_state_checksum": context_pack.base_repo_state_checksum,
        "root_cause": str(primary_output.get("root_cause", "")),
        "fix_strategy": str(primary_output.get("fix_strategy", "")),
        "rationale": str(primary_output.get("rationale", "")),
        "no_fix_reason": str(primary_output.get("no_fix_reason", "")),
        "abstention_reason": str(primary_output.get("abstention_reason", "")),
        "raw_output_ref": str(primary_output.get("raw_output_ref", "")),
        "deterministic_rule_id": str(primary_output.get("deterministic_rule_id", "")),
        "risk": str(primary_output.get("risk", "")),
        "confidence": float(primary_output.get("confidence", 0.0)),
        "reviewer_decision": str(reviewer_output.get("decision", "")),
        "reviewer_notes": list(reviewer_output.get("notes", [])),
        "final_diff_source": final_diff_source,
        "proposer_usability_reason": str(primary_output.get("usability_reason", "")),
        "reviewer_usability_reason": str(reviewer_output.get("usability_reason", "")),
        "policy_validation_checksum": "",
        "artifact_checksum": "",
        "created_at": utc_now_text(),
    }


def _compute_final_repair_artifact_checksum(payload: dict[str, Any]) -> str:
    stable = {
        k: v for k, v in payload.items()
        if k not in {"artifact_checksum", "created_at", "policy_validation_checksum"}
    }
    return sha256_canonical_json(stable)


# ── F5: Main producer ─────────────────────────────────────────────


def _persist_proposer_diagnostic(
    *,
    output_dir: Path,
    raw_content: str,
    schema_name: str,
    validation_error: str,
    finish_reason: Any = None,
    response_format: Any = None,
    model_metadata: dict[str, Any] | None = None,
) -> Path:
    """Persist a safe diagnostic artifact when proposer output is invalid.

    Captures role metadata, parsed JSON keys, content lengths, and the
    validation error reason — without leaking raw prompt, endpoint, or key data.
    """
    parsed_json: dict[str, Any] = {}
    try:
        parsed_json = json.loads(raw_content) if raw_content.strip() else {}
    except (json.JSONDecodeError, TypeError):
        pass

    changed_files = parsed_json.get("changed_files")
    changed_files_count = len(changed_files) if isinstance(changed_files, list) else 0
    proposed_diff = str(parsed_json.get("proposed_diff") or "")
    proposed_diff_preview = _safe_diagnostic_text(proposed_diff) if proposed_diff else ""
    for pattern in (
        "ghp_", "gho_", "ghu_", "ghs_", "ghr_",
        "-----BEGIN", "bearer ", "Bearer ",
    ):
        if pattern in proposed_diff_preview:
            proposed_diff_preview = "[REDACTED - pattern detected]"

    normalized_diff = proposed_diff.lstrip()
    if not proposed_diff.strip():
        proposed_diff_format = "empty"
    elif normalized_diff.startswith("diff --git"):
        proposed_diff_format = "git_unified_diff"
    elif normalized_diff.startswith("*** Begin Patch"):
        proposed_diff_format = "apply_patch"
    elif "```" in proposed_diff:
        proposed_diff_format = "markdown_fenced"
    else:
        proposed_diff_format = "unknown"

    safe_preview = _safe_diagnostic_text(raw_content) if raw_content else ""
    for pattern in (
        "ghp_", "gho_", "ghu_", "ghs_", "ghr_",
        "-----BEGIN", "bearer ", "Bearer ",
    ):
        if pattern in safe_preview:
            safe_preview = "[REDACTED - pattern detected]"

    diagnostic: dict[str, Any] = {
        "diagnostic_kind": "proposer_validation_failure",
        "role": "main",
        "responsibility": "repair_proposal",
        "schema_name": schema_name,
        "validation_error": validation_error,
        "parsed_keys": sorted(parsed_json.keys()) if isinstance(parsed_json, dict) else [],
        "raw_content_preview": safe_preview,
        "proposed_diff_length": len(proposed_diff),
        "proposed_diff_checksum": hashlib.sha256(proposed_diff.encode("utf-8")).hexdigest() if proposed_diff else "",
        "proposed_diff_format": proposed_diff_format,
        "proposed_diff_preview": proposed_diff_preview,
        "has_diff_git": normalized_diff.startswith("diff --git"),
        "has_old_file_marker": "--- a/" in proposed_diff or "--- " in proposed_diff,
        "has_new_file_marker": "+++ b/" in proposed_diff or "+++ " in proposed_diff,
        "has_hunk_marker": "@@" in proposed_diff,
        "has_apply_patch_begin": "*** Begin Patch" in proposed_diff,
        "has_apply_patch_update_file": "*** Update File:" in proposed_diff,
        "redacted_summary": {
            "root_cause": _safe_diagnostic_text(parsed_json.get("root_cause")),
            "fix_strategy": _safe_diagnostic_text(parsed_json.get("fix_strategy")),
            "no_fix_reason": _safe_diagnostic_text(parsed_json.get("no_fix_reason")),
            "changed_files_count": changed_files_count,
        },
        "finish_reason": str(finish_reason) if finish_reason is not None else "",
        "response_format_used": str(response_format) if response_format is not None else "",
        "model_metadata": model_metadata or {},
        "created_at": utc_now_text(),
    }
    path = output_dir / "repair_diagnostic_proposer.json"
    _write_json(path, diagnostic)
    return path


def _persist_reviewer_diagnostic(
    *,
    output_dir: Path,
    reviewer_result: Any,
    primary_failure_reason: str,
    fallback_failure_reason: str,
    timeout_occurred: bool,
    schema_validation_error: str,
    raw_content: str = "",
    primary_raw_content: str = "",
    fallback_raw_content: str = "",
) -> Path:
    configured_deployment = str(getattr(reviewer_result, "configured_deployment", "") or "")
    actual_deployment = str(getattr(reviewer_result, "actual_deployment", "") or "")
    fallback_deployment = str(getattr(reviewer_result, "fallback_deployment", "") or "")
    source = str(getattr(reviewer_result, "source", "") or "")
    fallback_used = bool(getattr(reviewer_result, "fallback_used", False)) or source == "deterministic"
    primary_http = str(getattr(reviewer_result, "primary_http_status", "") or "")
    fallback_http = str(getattr(reviewer_result, "fallback_http_status", "") or "")
    primary_raw = primary_raw_content or str(getattr(reviewer_result, "primary_raw_content", "") or "")
    fallback_raw = fallback_raw_content or str(getattr(reviewer_result, "fallback_raw_content", "") or "")
    safe_raw = _safe_diagnostic_text(raw_content or primary_raw or fallback_raw or str(getattr(reviewer_result, "content", "") or ""))[:4000]

    diagnostic: dict[str, Any] = {
        "diagnostic_kind": "reviewer_unavailable",
        "role": "reviewer",
        "configured_deployment": configured_deployment,
        "actual_deployment": actual_deployment,
        "fallback_attempted": bool(getattr(reviewer_result, "fallback_attempted", False)) or bool(fallback_deployment),
        "fallback_deployment": fallback_deployment,
        "fallback_used": fallback_used,
        "primary_success": False,
        "primary_failure_reason": primary_failure_reason or "",
        "fallback_failure_reason": fallback_failure_reason or "",
        "parser_failure_reason": str(getattr(reviewer_result, "parser_failure_reason", "") or ""),
        "timeout_occurred": timeout_occurred,
        "schema_validation_error": schema_validation_error or "",
        "raw_content_preview": safe_raw,
        "raw_content_preserved": bool(safe_raw),
        "primary_raw_content_preview": _safe_diagnostic_text(primary_raw)[:4000],
        "fallback_raw_content_preview": _safe_diagnostic_text(fallback_raw)[:4000],
        "primary_http_status": primary_http,
        "fallback_http_status": fallback_http,
        "model_status": str(getattr(reviewer_result, "model_status", "") or ""),
        "created_at": utc_now_text(),
    }
    path = output_dir / "repair_diagnostic_reviewer.json"
    _write_json(path, diagnostic)
    return path


def _persist_correction_diagnostic(
    *,
    output_dir: Path,
    raw_content: str,
    schema_name: str,
    validation_failures: list[str],
    coerced_output: dict[str, Any] | None,
    context_checksums: dict[str, str],
    technical_validation_passed: bool,
    finish_reason: Any = None,
    response_format: Any = None,
    model_metadata: dict[str, Any] | None = None,
) -> Path:
    """Persist a safe diagnostic artifact when correction fails.

    Follows the same redaction policy as _persist_proposer_diagnostic.
    exact_old_text / exact_new_text values are never persisted (lengths only).
    """
    parsed_json: dict[str, Any] = coerced_output or {}
    if coerced_output is None and raw_content.strip():
        try:
            parsed_json = json.loads(raw_content)
        except (json.JSONDecodeError, TypeError):
            pass

    proposed_diff = str(parsed_json.get("proposed_diff") or "")
    proposed_diff_preview = _safe_diagnostic_text(proposed_diff) if proposed_diff else ""
    for pattern in (
        "ghp_", "gho_", "ghu_", "ghs_", "ghr_",
        "-----BEGIN", "bearer ", "Bearer ",
    ):
        if pattern in proposed_diff_preview:
            proposed_diff_preview = "[REDACTED - pattern detected]"

    normalized_diff = proposed_diff.lstrip()
    if not proposed_diff.strip():
        proposed_diff_format = "empty"
    elif normalized_diff.startswith("diff --git"):
        proposed_diff_format = "git_unified_diff"
    elif normalized_diff.startswith("*** Begin Patch"):
        proposed_diff_format = "apply_patch"
    elif "```" in proposed_diff:
        proposed_diff_format = "markdown_fenced"
    else:
        proposed_diff_format = "unknown"

    safe_preview = _safe_diagnostic_text(raw_content) if raw_content else ""
    for pattern in (
        "ghp_", "gho_", "ghu_", "ghs_", "ghr_",
        "-----BEGIN", "bearer ", "Bearer ",
    ):
        if pattern in safe_preview:
            safe_preview = "[REDACTED - pattern detected]"

    raw_edits = parsed_json.get("proposed_edits") if isinstance(parsed_json, dict) else None
    structured_edits = _structured_edits(raw_edits)
    sanitized_edits: list[dict[str, Any]] = []
    expected_source_sha256_values: list[str] = []
    for edit in structured_edits:
        sanitized = {
            "path": edit.get("path", ""),
            "expected_source_sha256": edit.get("expected_source_sha256", ""),
            "exact_old_text_length": len(edit.get("exact_old_text", "")),
            "exact_new_text_length": len(edit.get("exact_new_text", "")),
        }
        sanitized_edits.append(sanitized)
        if edit.get("expected_source_sha256"):
            expected_source_sha256_values.append(edit["expected_source_sha256"])

    decision = str(parsed_json.get("decision") or "") if isinstance(parsed_json, dict) else ""

    diagnostic: dict[str, Any] = {
        "diagnostic_kind": "correction_validation_failure",
        "role": "reviewer_correction",
        "responsibility": "repair_correction",
        "schema_name": schema_name,
        "validation_failures": validation_failures,
        "raw_content_preview": safe_preview,
        "proposed_diff_preview": proposed_diff_preview,
        "proposed_diff_length": len(proposed_diff),
        "proposed_diff_checksum": hashlib.sha256(proposed_diff.encode("utf-8")).hexdigest() if proposed_diff else "",
        "proposed_diff_format": proposed_diff_format,
        "has_diff_git": normalized_diff.startswith("diff --git"),
        "has_old_file_marker": "--- a/" in proposed_diff or "--- " in proposed_diff,
        "has_new_file_marker": "+++ b/" in proposed_diff or "+++ " in proposed_diff,
        "has_hunk_marker": "@@" in proposed_diff,
        "has_apply_patch_begin": "*** Begin Patch" in proposed_diff,
        "has_apply_patch_update_file": "*** Update File:" in proposed_diff,
        "proposed_edits": sanitized_edits,
        "reviewer_decision": decision,
        "expected_source_sha256_values": expected_source_sha256_values,
        "context_checksums": context_checksums,
        "technical_validation_passed": technical_validation_passed,
        "finish_reason": str(finish_reason) if finish_reason is not None else "",
        "response_format_used": str(response_format) if response_format is not None else "",
        "model_metadata": model_metadata or {},
        "created_at": utc_now_text(),
    }
    path = output_dir / "correction_repair_llm_output.json"
    _write_json(path, diagnostic)
    return path


def produce_repair_review_chain(
    *,
    failure_evidence: FailureEvidence,
    context_pack: RepairContextPack,
    output_dir: Path,
    sandbox_path: str | Path | None = None,
    source_profile: str = "",
    target_profile: str = "",
    model_client: V2AssistantModelClient | None = None,
    invocation_ledger: Any = None,
) -> dict[str, Any]:
    """Produce proposer -> reviewer-final-author chain with technical fallback."""
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        repair_intelligence = run_repair_intelligence_preflight(
            failure_evidence=failure_evidence, context_pack=context_pack,
            sandbox_path=sandbox_path,
        )
    except Exception:
        logging.getLogger("repair_review_chain").exception(
            "repair_intelligence_abstained job_id=%s stage_index=%s",
            context_pack.job_id, context_pack.stage_index,
        )
        repair_intelligence = {}

    deterministic_payload = _build_deterministic_repair_payload(
        failure_evidence=failure_evidence,
        context_pack=context_pack,
        source_profile=source_profile,
        target_profile=target_profile,
        repair_intelligence=repair_intelligence,
    )
    deterministic_checksum = sha256_canonical_json(deterministic_payload)
    deterministic_path = output_dir / "deterministic_repair_artifact.json"
    _write_json(deterministic_path, deterministic_payload)

    client = model_client or V2AssistantModelClient()
    logger = logging.getLogger("repair_review_chain")
    logger.info("repair_proposer_started job_id=%s stage_index=%s", context_pack.job_id, context_pack.stage_index)

    # ── PR-G: Capture proposer invocation ────────────────────────────
    proposer_invocation_id: str | None = None
    reviewer_invocation_id: str | None = None
    if invocation_ledger is not None:
        context_checksum_for_ledger = getattr(context_pack, "context_pack_checksum", "") or ""
        proposer_invocation_id = invocation_ledger.start_invocation(
            job_id=context_pack.job_id,
            role="main",
            responsibility="repair_proposal",
            context_checksum=context_checksum_for_ledger,
            input_checksum=deterministic_checksum,
            schema_name="RepairPrimaryOutput",
        )

    # Primary Repair LLM (PROPOSER)
    primary_result = client.answer_with_role(
        role=V2ModelRole.PROPOSER,
        prompt=_primary_repair_prompt(
            context_pack, deterministic_checksum, repair_intelligence, failure_evidence
        ),
        fallback="Primary repair model unavailable; reviewed repair cannot be produced.",
        output_schema_name="RepairPrimaryOutput",
        require_schema=True,
    )

    fallback_used_primary = str(getattr(primary_result, "source", "") or "") == "deterministic"
    primary_ledger_meta = {
        "transport": getattr(primary_result, "transport", None) if isinstance(getattr(primary_result, "transport", None), str) else None,
        "http_status": getattr(primary_result, "primary_http_status", None) if isinstance(getattr(primary_result, "primary_http_status", None), str) else None,
        "azure_request_id": getattr(primary_result, "azure_request_id", None) if isinstance(getattr(primary_result, "azure_request_id", None), str) else None,
        "retry_count": getattr(primary_result, "retry_count", 0) if isinstance(getattr(primary_result, "retry_count", 0), int) else 0,
        "retry_after": getattr(primary_result, "retry_after", None) if isinstance(getattr(primary_result, "retry_after", None), str) else None,
        "response_format": getattr(primary_result, "response_format_used", None) if isinstance(getattr(primary_result, "response_format_used", None), str) else None,
        "parse_result": "accepted" if primary_result.success else "rejected",
    }
    logger.info("repair_proposer_completed job_id=%s success=%s", context_pack.job_id, bool(primary_result.success))

    primary_output: dict[str, Any] | None = None
    primary_failures: list[str] = []
    if not primary_result.success:
        if proposer_invocation_id is not None:
            invocation_ledger.fail_invocation(
                proposer_invocation_id,
                redacted_error=primary_result.failure_reason,
                redacted_summary=primary_result.redacted_summary,
                fallback_used=fallback_used_primary,
                **primary_ledger_meta,
            )
        primary_output = _unusable_primary_output(
            f"proposer unavailable: {primary_result.failure_reason or primary_result.model_status}"
        )

    try:
        if primary_output is None:
            primary_output = _coerce_primary_repair_output(primary_result.content)
            primary_failures = _validate_primary_repair_output(primary_output)
    except RepairReviewChainProductionError as exc:
        validation_error = str(exc)
        _persist_proposer_diagnostic(
            output_dir=output_dir,
            raw_content=primary_result.content,
            schema_name="RepairPrimaryOutput",
            validation_error=validation_error,
            finish_reason=getattr(primary_result, "finish_reason", None),
            response_format=getattr(primary_result, "response_format_used", None),
            model_metadata={
                "source": getattr(primary_result, "source", ""),
                "model_status": getattr(primary_result, "model_status", ""),
                "provider": getattr(primary_result, "provider", ""),
                "role": getattr(primary_result, "role", ""),
                "configured_max_input_tokens": getattr(primary_result, "configured_max_input_tokens", 0),
                "configured_max_output_tokens": getattr(primary_result, "configured_max_output_tokens", 0),
                "response_format_used": getattr(primary_result, "response_format_used", ""),
            },
        )
        if proposer_invocation_id is not None:
            invocation_ledger.fail_invocation(
                proposer_invocation_id,
                redacted_error=validation_error,
                redacted_summary=primary_result.redacted_summary,
                fallback_used=fallback_used_primary,
                **primary_ledger_meta,
            )
        preserved = {}
        try:
            parsed = json.loads(primary_result.content)
            if isinstance(parsed, dict):
                for key in ("root_cause", "fix_strategy", "rationale", "no_fix_reason", "abstention_reason", "raw_output_ref", "confidence", "changed_files", "proposed_diff", "deterministic_rule_id", "risk"):
                    if key in parsed:
                        preserved[key] = parsed[key]
        except (json.JSONDecodeError, TypeError):
            pass
        primary_output = _unusable_primary_output(_safe_diagnostic_text(validation_error), primary_result.content, preserved_fields=preserved)
        primary_failures = []

    if primary_failures:
        validation_error = "invalid primary repair output: " + "; ".join(primary_failures)
        _persist_proposer_diagnostic(
            output_dir=output_dir,
            raw_content=primary_result.content,
            schema_name="RepairPrimaryOutput",
            validation_error=validation_error,
            finish_reason=getattr(primary_result, "finish_reason", None),
            response_format=getattr(primary_result, "response_format_used", None),
            model_metadata={
                "source": getattr(primary_result, "source", ""),
                "model_status": getattr(primary_result, "model_status", ""),
                "provider": getattr(primary_result, "provider", ""),
                "role": getattr(primary_result, "role", ""),
                "configured_max_input_tokens": getattr(primary_result, "configured_max_input_tokens", 0),
                "configured_max_output_tokens": getattr(primary_result, "configured_max_output_tokens", 0),
                "response_format_used": getattr(primary_result, "response_format_used", ""),
            },
        )
        if proposer_invocation_id is not None:
            invocation_ledger.fail_invocation(
                proposer_invocation_id,
                redacted_error=validation_error,
                redacted_summary=primary_result.redacted_summary,
                fallback_used=fallback_used_primary,
                **primary_ledger_meta,
            )
        primary_output["usability_reason"] = _safe_diagnostic_text(validation_error)
        if primary_output.get("no_fix_reason"):
            primary_output["abstention_reason"] = _safe_diagnostic_text(primary_output["no_fix_reason"])
            primary_output["usability_reason"] = "MODEL_INSUFFICIENT_EVIDENCE_ABSTENTION"

    if proposer_invocation_id is not None and primary_result.success and not primary_failures:
        invocation_ledger.complete_invocation(
            proposer_invocation_id,
            output=primary_result.content,
            redacted_summary=primary_result.redacted_summary,
            fallback_used=fallback_used_primary,
            **primary_ledger_meta,
        )

    primary_candidate_diff = ""
    primary_candidate_failures: list[str] = list(primary_failures)
    if not primary_candidate_failures:
        primary_candidate_diff, candidate_failures, candidate_paths = _validate_model_candidate(
            output=primary_output,
            role="proposer",
            context_pack=context_pack,
            sandbox_path=sandbox_path,
            output_dir=output_dir,
        )
        primary_candidate_failures.extend(candidate_failures)
        if not primary_candidate_failures and primary_candidate_diff:
            primary_candidate_failures.extend(_validate_pom_v1_candidate(
                proposed_diff=primary_candidate_diff,
                changed_files=candidate_paths,
                intelligence=repair_intelligence,
                context_pack=context_pack,
                sandbox_path=sandbox_path,
            ))
        if not primary_candidate_failures and primary_candidate_diff:
            primary_output["proposed_diff"] = primary_candidate_diff
            primary_output["changed_files"] = candidate_paths
    primary_checksum = _compute_primary_repair_checksum(primary_output)
    primary_output["output_checksum"] = primary_checksum
    primary_path = output_dir / "primary_repair_llm_output.json"
    primary_output["raw_output_ref"] = str(primary_path)
    _write_json(primary_path, primary_output)

    context_checksum = context_pack.context_pack_checksum
    proposed_diff = str(primary_output.get("proposed_diff", ""))
    diff_checksum = sha256_canonical_json({"unified_diff": proposed_diff})

    # ── PR-G: Capture reviewer invocation ────────────────────────────
    if invocation_ledger is not None:
        reviewer_invocation_id = invocation_ledger.start_invocation(
            job_id=context_pack.job_id,
            role="reviewer",
            responsibility="repair_review",
            context_checksum=context_checksum,
            input_checksum=primary_checksum,
            schema_name="RepairReviewerOutput",
        )

    # Reviewer Repair LLM (REVIEWER)
    reviewer_result = client.answer_with_role(
        role=V2ModelRole.REVIEWER,
        prompt=_reviewer_repair_prompt(
            primary_output,
            failure_evidence,
            context_pack,
            deterministic_checksum,
            context_checksum,
            primary_checksum,
            diff_checksum,
            repair_intelligence,
        ),
        fallback="Reviewer repair model unavailable; reviewed repair cannot be produced.",
        output_schema_name="RepairReviewerOutput",
        require_schema=True,
    )

    # ── PR-G: Complete/fail reviewer invocation ──────────────────────
    fallback_used_reviewer = str(getattr(reviewer_result, "source", "") or "") == "deterministic"
    reviewer_ledger_meta = {
        "transport": getattr(reviewer_result, "transport", None) if isinstance(getattr(reviewer_result, "transport", None), str) else None,
        "http_status": getattr(reviewer_result, "primary_http_status", None) if isinstance(getattr(reviewer_result, "primary_http_status", None), str) else None,
        "azure_request_id": getattr(reviewer_result, "azure_request_id", None) if isinstance(getattr(reviewer_result, "azure_request_id", None), str) else None,
        "retry_count": getattr(reviewer_result, "retry_count", 0) if isinstance(getattr(reviewer_result, "retry_count", 0), int) else 0,
        "retry_after": getattr(reviewer_result, "retry_after", None) if isinstance(getattr(reviewer_result, "retry_after", None), str) else None,
        "response_format": getattr(reviewer_result, "response_format_used", None) if isinstance(getattr(reviewer_result, "response_format_used", None), str) else None,
        "parse_result": "accepted" if reviewer_result.success else "rejected",
    }
    if reviewer_invocation_id is not None:
        if reviewer_result.success:
            invocation_ledger.complete_invocation(
                reviewer_invocation_id,
                output=reviewer_result.content,
                redacted_summary=reviewer_result.redacted_summary,
                fallback_used=fallback_used_reviewer,
                **reviewer_ledger_meta,
            )
        else:
            invocation_ledger.fail_invocation(
                reviewer_invocation_id,
                redacted_error=reviewer_result.failure_reason,
                redacted_summary=reviewer_result.redacted_summary,
                fallback_used=fallback_used_reviewer,
                **reviewer_ledger_meta,
            )
        if bool(getattr(reviewer_result, "fallback_attempted", False)):
            fallback_invocation_id = invocation_ledger.start_invocation(
                job_id=context_pack.job_id,
                role="fallback",
                responsibility="repair_review",
                context_checksum=context_checksum,
                input_checksum=primary_checksum,
                schema_name="RepairReviewerOutput",
                fallback_parent_invocation_id=reviewer_invocation_id,
            )
            if reviewer_result.success and bool(getattr(reviewer_result, "fallback_used", False)):
                invocation_ledger.complete_invocation(
                    fallback_invocation_id,
                    output=reviewer_result.content,
                    redacted_summary=reviewer_result.redacted_summary,
                    fallback_used=True,
                    **reviewer_ledger_meta,
                )
            else:
                invocation_ledger.fail_invocation(
                    fallback_invocation_id,
                    redacted_error=getattr(reviewer_result, "fallback_failure_reason", "") or reviewer_result.failure_reason,
                    redacted_summary=reviewer_result.redacted_summary,
                    fallback_used=True,
                    **reviewer_ledger_meta,
                )

    reviewer_output: dict[str, Any] = {}
    reviewer_reason = ""
    if not reviewer_result.success:
        reviewer_reason = f"reviewer unavailable: {reviewer_result.failure_reason or reviewer_result.model_status}"
        _persist_reviewer_diagnostic(
            output_dir=output_dir,
            reviewer_result=reviewer_result,
            primary_failure_reason=str(getattr(reviewer_result, "primary_failure_reason", "") or ""),
            fallback_failure_reason=str(getattr(reviewer_result, "fallback_failure_reason", "") or ""),
            timeout_occurred=bool(getattr(reviewer_result, "timeout_occurred", False)),
            schema_validation_error=str(getattr(reviewer_result, "schema_validation_error", "") or ""),
            raw_content=str(getattr(reviewer_result, "content", "") or ""),
            primary_raw_content=str(getattr(reviewer_result, "primary_raw_content", "") or ""),
            fallback_raw_content=str(getattr(reviewer_result, "fallback_raw_content", "") or ""),
        )
    else:
        try:
            reviewer_output = _coerce_reviewer_repair_output(
                reviewer_result.content, deterministic_checksum, context_checksum,
                primary_checksum, diff_checksum,
            )
            checksum_failures = []
            if reviewer_output["reviewed_context_checksum"] != context_checksum:
                checksum_failures.append("reviewer context checksum mismatch")
            if reviewer_output["reviewed_primary_output_checksum"] != primary_checksum:
                checksum_failures.append("reviewer primary checksum mismatch")
            if reviewer_output["reviewed_diff_checksum"] != diff_checksum:
                checksum_failures.append("reviewer proposer-diff checksum mismatch")
            if checksum_failures:
                reviewer_reason = "; ".join(checksum_failures)
            else:
                reviewer_candidate_diff, reviewer_failures, reviewer_paths = _validate_model_candidate(
                    output=reviewer_output,
                    role="reviewer",
                    context_pack=context_pack,
                    sandbox_path=sandbox_path,
                    output_dir=output_dir,
                )
                reviewer_failures.extend(_validate_pom_v1_candidate(
                    proposed_diff=reviewer_candidate_diff,
                    changed_files=reviewer_paths,
                    intelligence=repair_intelligence,
                    context_pack=context_pack,
                    sandbox_path=sandbox_path,
                ))
                if reviewer_failures:
                    reviewer_reason = "; ".join(reviewer_failures)
                else:
                    reviewer_output["proposed_diff"] = reviewer_candidate_diff
                    reviewer_output["changed_files"] = reviewer_paths
        except RepairReviewChainProductionError as exc:
            reviewer_reason = _safe_diagnostic_text(str(exc))
    reviewer_output["usability_reason"] = reviewer_reason
    reviewer_checksum = _compute_reviewer_repair_checksum(reviewer_output) if reviewer_output else ""
    if reviewer_output:
        reviewer_output["output_checksum"] = reviewer_checksum
    reviewer_path = output_dir / "reviewer_repair_llm_output.json"
    if reviewer_output:
        _write_json(reviewer_path, reviewer_output)

    proposer_diff = str(primary_output.get("proposed_diff") or "")
    proposer_failures = list(primary_candidate_failures)
    proposer_reason = "; ".join(proposer_failures)
    primary_output["usability_reason"] = proposer_reason
    if not proposer_reason and str(primary_output.get("no_fix_reason", "")).strip() and not proposer_diff.strip():
        primary_output["abstention_reason"] = str(primary_output["no_fix_reason"])
        primary_output["usability_reason"] = "MODEL_INSUFFICIENT_EVIDENCE_ABSTENTION"
    final_diff = str(reviewer_output.get("proposed_diff") or "") if not reviewer_reason else ""
    final_diff_source = "reviewer" if final_diff else "proposer_fallback" if not proposer_reason else ""
    if not final_diff and not proposer_reason:
        final_diff = proposer_diff
    final_diff = _normalize_unified_diff_hunk_counts(final_diff)
    selected_changed_files = (
        list(reviewer_output.get("changed_files") or [])
        if final_diff_source == "reviewer"
        else list(primary_output.get("changed_files") or [])
    )
    logger.info("repair_reviewer_completed job_id=%s success=%s", context_pack.job_id, bool(reviewer_result.success))
    correction_attempts = 0
    final_failures = _candidate_source_validation(
        proposed_diff=final_diff,
        changed_files=selected_changed_files,
        context_pack=context_pack,
        sandbox_path=sandbox_path,
    ) if final_diff else ["no candidate diff available for final validation"]
    if not final_failures and final_diff:
        final_failures.extend(_validate_pom_v1_candidate(
            proposed_diff=final_diff,
            changed_files=selected_changed_files,
            intelligence=repair_intelligence,
            context_pack=context_pack,
            sandbox_path=sandbox_path,
        ))
    if not final_failures and final_diff:
        final_failures.extend(_strict_git_applicability(
            proposed_diff=final_diff,
            sandbox_path=sandbox_path,
            output_dir=output_dir,
        ))

    if final_failures:
        correction_attempts = 1
        correction_result = client.answer_with_role(
            role=V2ModelRole.REVIEWER,
            prompt=_candidate_correction_prompt(
                context_pack=context_pack,
                candidate_diff=final_diff,
                failures=final_failures,
                context_checksum=context_checksum,
                primary_checksum=primary_checksum,
                diff_checksum=diff_checksum,
                authoritative_facts=repair_intelligence,
            ),
            fallback="Corrective repair model unavailable; reviewed repair cannot be produced.",
            output_schema_name="RepairReviewerOutput",
            require_schema=True,
        )
        correction_finish = getattr(correction_result, "finish_reason", None)
        correction_format = getattr(correction_result, "response_format_used", None)
        correction_meta = {
            "source": getattr(correction_result, "source", ""),
            "model_status": getattr(correction_result, "model_status", ""),
            "provider": getattr(correction_result, "provider", ""),
            "role": getattr(correction_result, "role", ""),
            "configured_max_input_tokens": getattr(correction_result, "configured_max_input_tokens", 0),
            "configured_max_output_tokens": getattr(correction_result, "configured_max_output_tokens", 0),
            "response_format_used": getattr(correction_result, "response_format_used", ""),
        }
        if correction_result.success:
            try:
                corrected_output = _coerce_reviewer_repair_output(
                    correction_result.content,
                    deterministic_checksum,
                    context_checksum,
                    primary_checksum,
                    diff_checksum,
                )
                checksum_failures = []
                if corrected_output["reviewed_context_checksum"] != context_checksum:
                    checksum_failures.append("correction context checksum mismatch")
                if corrected_output["reviewed_primary_output_checksum"] != primary_checksum:
                    checksum_failures.append("correction primary checksum mismatch")
                if corrected_output["reviewed_diff_checksum"] != diff_checksum:
                    checksum_failures.append("correction proposer-diff checksum mismatch")
                corrected_diff, corrected_failures, corrected_paths = _validate_model_candidate(
                    output=corrected_output,
                    role="reviewer correction",
                    context_pack=context_pack,
                    sandbox_path=sandbox_path,
                    output_dir=output_dir,
                )
                final_failures = checksum_failures + corrected_failures
                if not final_failures:
                    final_failures.extend(_validate_pom_v1_candidate(
                        proposed_diff=corrected_diff,
                        changed_files=corrected_paths,
                        intelligence=repair_intelligence,
                        context_pack=context_pack,
                        sandbox_path=sandbox_path,
                    ))
                if not final_failures:
                    corrected_output["proposed_diff"] = corrected_diff
                    corrected_output["changed_files"] = corrected_paths
                    reviewer_output = corrected_output
                    reviewer_reason = ""
                    reviewer_checksum = _compute_reviewer_repair_checksum(reviewer_output)
                    reviewer_output["output_checksum"] = reviewer_checksum
                    final_diff = corrected_diff
                    final_diff_source = "reviewer_correction"
                    selected_changed_files = corrected_paths
                else:
                    _persist_correction_diagnostic(
                        output_dir=output_dir,
                        raw_content=correction_result.content,
                        schema_name="RepairReviewerOutput",
                        validation_failures=final_failures,
                        coerced_output=corrected_output,
                        context_checksums={
                            "context_pack_checksum": context_checksum,
                            "primary_checksum": primary_checksum,
                            "diff_checksum": diff_checksum,
                        },
                        technical_validation_passed=False,
                        finish_reason=correction_finish,
                        response_format=correction_format,
                        model_metadata=correction_meta,
                    )
            except RepairReviewChainProductionError as exc:
                final_failures = [_safe_diagnostic_text(str(exc))]
                _persist_correction_diagnostic(
                    output_dir=output_dir,
                    raw_content=correction_result.content,
                    schema_name="RepairReviewerOutput",
                    validation_failures=final_failures,
                    coerced_output=None,
                    context_checksums={
                        "context_pack_checksum": context_checksum,
                        "primary_checksum": primary_checksum,
                        "diff_checksum": diff_checksum,
                    },
                    technical_validation_passed=False,
                    finish_reason=correction_finish,
                    response_format=correction_format,
                    model_metadata=correction_meta,
                )
        else:
            final_failures = [
                f"correction reviewer unavailable: {correction_result.failure_reason or correction_result.model_status}"
            ]
            _persist_correction_diagnostic(
                output_dir=output_dir,
                raw_content=correction_result.content,
                schema_name="RepairReviewerOutput",
                validation_failures=final_failures,
                coerced_output=None,
                context_checksums={
                    "context_pack_checksum": context_checksum,
                    "primary_checksum": primary_checksum,
                    "diff_checksum": diff_checksum,
                },
                technical_validation_passed=False,
                finish_reason=correction_finish,
                response_format=correction_format,
                model_metadata=correction_meta,
            )

    if not final_diff:
        generation_reason = "; ".join(filter(None, [
            proposer_reason,
            reviewer_reason,
            "; ".join(final_failures),
            "no technically usable final diff",
        ]))
        return {"artifact_refs": {
            "deterministic_artifact": str(deterministic_path),
            "primary_llm_output": str(primary_path),
            "reviewer_llm_output": str(reviewer_path) if reviewer_output else "",
        }, "review_chain": {
            "generation_status": "repair_generation_failed",
            "generation_failure_reason": generation_reason,
            "proposer_usability_reason": proposer_reason,
            "reviewer_usability_reason": reviewer_reason,
            "proposer_diff_usable": not bool(proposer_reason),
            "reviewer_diff_usable": not bool(reviewer_reason) and bool(reviewer_output.get("proposed_diff") or reviewer_output.get("proposed_edits")),
            "correction_attempts": correction_attempts,
            "final_validation_failures": final_failures,
            "reviewer_output_checksum": reviewer_checksum,
            "final_diff_source": "", "final_diff_ref": "",
            "model_roles": {"proposer": _safe_model_role_status(primary_result), "reviewer": _safe_model_role_status(reviewer_result)},
            "proposer_invocation_id": proposer_invocation_id,
            "reviewer_invocation_id": reviewer_invocation_id,
            "root_cause": str(primary_output.get("root_cause", "")),
            "fix_strategy": str(primary_output.get("fix_strategy", "")),
            "rationale": str(primary_output.get("rationale", "")),
            "confidence": float(primary_output.get("confidence", 0.0)),
            "no_fix_reason": str(primary_output.get("no_fix_reason", "")),
            "abstention_reason": str(primary_output.get("abstention_reason", "")),
            "raw_output_ref": str(primary_output.get("raw_output_ref", "")),
        }}
    if final_failures:
        return {"artifact_refs": {
            "deterministic_artifact": str(deterministic_path),
            "primary_llm_output": str(primary_path),
            "reviewer_llm_output": str(reviewer_path) if reviewer_output else "",
        }, "review_chain": {
            "generation_status": "repair_generation_failed",
            "generation_failure_reason": "; ".join(final_failures),
            "proposer_usability_reason": proposer_reason,
            "reviewer_usability_reason": reviewer_reason,
            "proposer_diff_usable": not bool(proposer_reason),
            "reviewer_diff_usable": not bool(reviewer_reason) and bool(reviewer_output.get("proposed_diff") or reviewer_output.get("proposed_edits")),
            "correction_attempts": correction_attempts,
            "final_validation_failures": final_failures,
            "reviewer_output_checksum": reviewer_checksum,
            "final_diff_source": "", "final_diff_ref": "",
            "model_roles": {"proposer": _safe_model_role_status(primary_result), "reviewer": _safe_model_role_status(reviewer_result)},
            "proposer_invocation_id": proposer_invocation_id,
            "reviewer_invocation_id": reviewer_invocation_id,
            "root_cause": str(primary_output.get("root_cause", "")),
            "fix_strategy": str(primary_output.get("fix_strategy", "")),
            "rationale": str(primary_output.get("rationale", "")),
            "confidence": float(primary_output.get("confidence", 0.0)),
            "no_fix_reason": str(primary_output.get("no_fix_reason", "")),
            "abstention_reason": str(primary_output.get("abstention_reason", "")),
            "raw_output_ref": str(primary_output.get("raw_output_ref", "")),
        }}
    if not final_diff.endswith(("\n", "\r")):
        final_diff += "\n"
    if reviewer_output:
        _write_json(reviewer_path, reviewer_output)
    selected_diff_checksum = sha256_canonical_json({"unified_diff": final_diff})

    final_artifact = _build_final_reviewed_repair_artifact(
        job_id=context_pack.job_id,
        stage_index=context_pack.stage_index,
        failure_evidence=failure_evidence,
        context_pack=context_pack,
        primary_output=primary_output,
        primary_checksum=primary_checksum,
        reviewer_output=reviewer_output,
        reviewer_checksum=reviewer_checksum,
        deterministic_checksum=deterministic_checksum,
        selected_diff=final_diff,
        final_diff_source=final_diff_source,
        selected_changed_files=selected_changed_files,
    )
    final_artifact_checksum = _compute_final_repair_artifact_checksum(final_artifact)
    final_artifact["artifact_checksum"] = final_artifact_checksum
    final_artifact_path = output_dir / "final_reviewed_repair_artifact.json"
    _write_json(final_artifact_path, final_artifact)

    diff_path = output_dir / "final_reviewed_repair.diff"
    diff_path.write_bytes(final_diff.encode("utf-8"))

    review_chain: dict[str, Any] = {
        "deterministic_artifact_checksum": deterministic_checksum,
        "context_pack_checksum": context_checksum,
        "primary_output_checksum": primary_checksum,
        "reviewer_output_checksum": reviewer_checksum,
        "proposed_diff_checksum": selected_diff_checksum,
        "final_artifact_checksum": final_artifact_checksum,
        "reviewer_decision": reviewer_output.get("decision", ""),
        "final_diff_source": final_diff_source,
        "generation_status": "ready",
        "proposer_usability_reason": proposer_reason,
        "reviewer_usability_reason": reviewer_reason,
        "proposer_diff_usable": not bool(proposer_reason),
        "reviewer_diff_usable": not bool(reviewer_reason) and bool(reviewer_output.get("proposed_diff") or reviewer_output.get("proposed_edits")),
        "correction_attempts": correction_attempts,
        "final_validation_failures": final_failures,
        "job_id": context_pack.job_id,
        "stage_index": context_pack.stage_index,
        "deterministic_rule_id": str(final_artifact.get("deterministic_rule_id", "")),
        "risk": str(final_artifact.get("risk", "")),
        "root_cause": str(final_artifact.get("root_cause", "")),
        "fix_strategy": str(final_artifact.get("fix_strategy", "")),
        "rationale": str(final_artifact.get("rationale", "")),
        "no_fix_reason": str(final_artifact.get("no_fix_reason", "")),
        "abstention_reason": str(final_artifact.get("abstention_reason", "")),
        "raw_output_ref": str(final_artifact.get("raw_output_ref", "")),
        "changed_files": list(final_artifact.get("changed_files", [])),
        "confidence": float(final_artifact.get("confidence", 0.0)),
        "reviewer_notes": list(final_artifact.get("reviewer_notes", [])),
        "policy_validation_checksum": str(final_artifact.get("policy_validation_checksum", "")),
        "deterministic_artifact_ref": str(deterministic_path),
        "primary_output_ref": str(primary_path),
        "reviewer_output_ref": str(reviewer_path),
        "final_artifact_ref": str(final_artifact_path),
        "final_diff_ref": str(diff_path),
        "model_roles": {
            "proposer": _safe_model_role_status(primary_result),
            "reviewer": _safe_model_role_status(reviewer_result),
        },
    }
    if proposer_invocation_id is not None and reviewer_invocation_id is not None:
        review_chain["proposer_invocation_id"] = proposer_invocation_id
        review_chain["reviewer_invocation_id"] = reviewer_invocation_id
        assert proposer_invocation_id != reviewer_invocation_id, "proposer and reviewer invocation IDs must be distinct"
    review_chain_path = output_dir / "review_chain.json"
    _write_json(review_chain_path, review_chain)

    produced_refs = {
        "deterministic_artifact": str(deterministic_path),
        "primary_llm_output": str(primary_path),
        "reviewer_llm_output": str(reviewer_path),
        "final_reviewed_artifact": str(final_artifact_path),
        "final_reviewed_diff": str(diff_path),
        "review_chain_metadata": str(review_chain_path),
    }

    return {"artifact_refs": produced_refs, "review_chain": review_chain}


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2, default=str),
        encoding="utf-8",
    )


def _safe_model_role_status(result: Any) -> dict[str, Any]:
    """Safe role metadata; deployment names contain no endpoint/key material."""
    return {
        "role": str(getattr(result, "role", "") or ""),
        "available": bool(getattr(result, "success", False)),
        "status": "available" if bool(getattr(result, "success", False)) else "blocked",
        "fallback_used": bool(getattr(result, "fallback_used", False)) or str(getattr(result, "source", "") or "") == "azure_openai_fallback",
        "configured_deployment": str(getattr(result, "configured_deployment", "") or ""),
        "actual_deployment": str(getattr(result, "actual_deployment", "") or ""),
        "fallback_deployment": str(getattr(result, "fallback_deployment", "") or ""),
        "primary_failure_reason": str(getattr(result, "primary_failure_reason", "") or ""),
        "fallback_failure_reason": str(getattr(result, "fallback_failure_reason", "") or ""),
        "parser_failure_reason": str(getattr(result, "parser_failure_reason", "") or ""),
        "fallback_attempted": bool(getattr(result, "fallback_attempted", False)),
        "fallback_used": bool(getattr(result, "fallback_used", False)),
        "timeout_occurred": bool(getattr(result, "timeout_occurred", False)),
        "primary_http_status": str(getattr(result, "primary_http_status", "") or ""),
        "fallback_http_status": str(getattr(result, "fallback_http_status", "") or ""),
        "schema_validation_error": str(getattr(result, "schema_validation_error", "") or ""),
    }
