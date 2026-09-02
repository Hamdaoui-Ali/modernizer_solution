"""Safe, read-only unified diff preview parsing and sanitization."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

from migration_factory.control_tower.application.redaction import (
    redact_patch_preview,
    redact_model_summary,
)
from migration_factory.control_tower.domain.checksums import sha256_hex


MAX_FILES = 20
MAX_HUNKS_PER_FILE = 30
MAX_LINES_PER_HUNK = 200
MAX_TOTAL_LINES = 3000
MAX_LINE_LENGTH = 300
MAX_TOTAL_BYTES = 200 * 1024

_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_lines>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_lines>\d+))? @@(?P<section>.*)$"
)
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_UNC_PATH_RE = re.compile(r"^(?:\\\\|//)[^\\/]+[\\/][^\\/]+")


@dataclass(frozen=True)
class SafeDiffLine:
    kind: str
    old_line_number: int | None
    new_line_number: int | None
    text: str
    redacted: bool


@dataclass(frozen=True)
class SafeDiffHunk:
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    section_header: str | None
    lines: list[SafeDiffLine] = field(default_factory=list)


@dataclass(frozen=True)
class SafeDiffFile:
    path: str
    change_type: str
    additions: int
    deletions: int
    hunks: list[SafeDiffHunk] = field(default_factory=list)
    truncated: bool = False


@dataclass(frozen=True)
class SafeDiffPreview:
    proposal_id: str
    diff_ref: str | None
    diff_checksum: str
    files: list[SafeDiffFile] = field(default_factory=list)
    total_additions: int = 0
    total_deletions: int = 0
    truncated: bool = False
    checksum_mismatch: bool = False
    parse_status: str = "ok"
    redactions: list[str] = field(default_factory=list)


def build_safe_diff_preview(
    *,
    proposal_id: str,
    diff_ref: str | Path | None,
    diff_text: str | None = None,
    stored_diff_checksum: str | None = None,
) -> SafeDiffPreview:
    """Parse and sanitize a reviewed diff into a bounded preview.

    If stored_diff_checksum is provided and differs from the SHA-256
    of the loaded diff bytes, the preview carries checksum_mismatch=True.
    """
    raw_diff_bytes = _load_diff_bytes(diff_ref=diff_ref, diff_text=diff_text)
    diff_checksum = sha256_hex(raw_diff_bytes)
    checksum_mismatch = (
        stored_diff_checksum is not None
        and stored_diff_checksum != diff_checksum
    )
    preview_bytes = raw_diff_bytes[:MAX_TOTAL_BYTES]
    preview_text = preview_bytes.decode("utf-8", errors="replace")
    parsed = _SafeDiffParser(proposal_id=proposal_id, diff_ref=diff_ref, diff_checksum=diff_checksum)
    parsed._checksum_mismatch = checksum_mismatch
    parsed.parse(preview_text)
    if len(raw_diff_bytes) > MAX_TOTAL_BYTES:
        parsed.truncate("diff truncated to 200KB preview window")
    return parsed.build()


def safe_diff_preview_to_dict(preview: SafeDiffPreview) -> dict[str, Any]:
    return {
        "proposal_id": preview.proposal_id,
        "diff_ref": preview.diff_ref,
        "diff_checksum": preview.diff_checksum,
        "files": [_safe_diff_file_to_dict(file) for file in preview.files],
        "total_additions": preview.total_additions,
        "total_deletions": preview.total_deletions,
        "truncated": preview.truncated,
        "checksum_mismatch": preview.checksum_mismatch,
        "parse_status": preview.parse_status,
        "redactions": list(preview.redactions),
    }


def _safe_diff_file_to_dict(file: SafeDiffFile) -> dict[str, Any]:
    return {
        "path": file.path,
        "change_type": file.change_type,
        "additions": file.additions,
        "deletions": file.deletions,
        "hunks": [_safe_diff_hunk_to_dict(hunk) for hunk in file.hunks],
        "truncated": file.truncated,
    }


def _safe_diff_hunk_to_dict(hunk: SafeDiffHunk) -> dict[str, Any]:
    return {
        "old_start": hunk.old_start,
        "old_lines": hunk.old_lines,
        "new_start": hunk.new_start,
        "new_lines": hunk.new_lines,
        "section_header": hunk.section_header,
        "lines": [_safe_diff_line_to_dict(line) for line in hunk.lines],
    }


def _safe_diff_line_to_dict(line: SafeDiffLine) -> dict[str, Any]:
    return {
        "kind": line.kind,
        "old_line_number": line.old_line_number,
        "new_line_number": line.new_line_number,
        "text": line.text,
        "redacted": line.redacted,
    }


def _load_diff_bytes(*, diff_ref: str | Path | None, diff_text: str | None) -> bytes:
    if diff_text is not None:
        return diff_text.encode("utf-8")
    if diff_ref is None:
        return b""
    return Path(diff_ref).read_bytes()


class _SafeDiffParser:
    def __init__(self, *, proposal_id: str, diff_ref: str | Path | None, diff_checksum: str) -> None:
        self._proposal_id = proposal_id
        self._diff_ref = _safe_diff_ref(diff_ref)
        self._diff_checksum = diff_checksum
        self._checksum_mismatch = False
        self._files: list[SafeDiffFile] = []
        self._redactions: list[str] = []
        self._truncated = False
        self._total_additions = 0
        self._total_deletions = 0
        self._total_lines = 0
        self._current: _CurrentFileState | None = None

    def parse(self, diff_text: str) -> None:
        for raw_line in diff_text.splitlines():
            if self._truncated:
                break
            if self._total_lines >= MAX_TOTAL_LINES:
                self.truncate("diff truncated after 3000 total lines")
                break
            self._total_lines += 1

            if raw_line.startswith("diff --git "):
                self._start_file(raw_line)
                continue

            if self._current is None:
                continue

            if raw_line.startswith("Binary files ") or raw_line == "GIT binary patch":
                self._current.binary = True
                self._current.truncated = True
                self._current.skip_hunks = True
                self._add_redaction("binary diff encountered")
                continue

            if raw_line.startswith("rename from "):
                self._current.rename_from = raw_line[len("rename from ") :].strip()
                continue

            if raw_line.startswith("rename to "):
                self._current.rename_to = raw_line[len("rename to ") :].strip()
                continue

            if raw_line.startswith("--- "):
                self._current.old_path = raw_line[4:].strip()
                continue

            if raw_line.startswith("+++ "):
                self._current.new_path = raw_line[4:].strip()
                continue

            match = _HUNK_HEADER_RE.match(raw_line)
            if match is not None:
                self._start_hunk(match)
                continue

            if self._current.current_hunk is None or self._current.skip_hunks:
                continue

            self._append_hunk_line(raw_line)

        self._finalize_current_file()

    def build(self) -> SafeDiffPreview:
        return SafeDiffPreview(
            proposal_id=self._proposal_id,
            diff_ref=self._diff_ref,
            diff_checksum=self._diff_checksum,
            files=self._files,
            total_additions=self._total_additions,
            total_deletions=self._total_deletions,
            truncated=self._truncated or any(file.truncated for file in self._files),
            checksum_mismatch=self._checksum_mismatch,
            parse_status="ok" if self._files else "unparseable",
            redactions=self._redactions,
        )

    def truncate(self, reason: str) -> None:
        self._truncated = True
        self._add_redaction(reason)
        if self._current is not None:
            self._current.truncated = True

    def _start_file(self, header_line: str) -> None:
        self._finalize_current_file()
        if self._truncated or len(self._files) >= MAX_FILES:
            self.truncate("diff truncated after 20 files")
            return

        parts = header_line[len("diff --git ") :].split(" ")
        if len(parts) >= 2:
            raw_old = parts[0].strip()
            raw_new = parts[1].strip()
        else:
            raw_old = ""
            raw_new = ""
        self._current = _CurrentFileState(raw_old=raw_old, raw_new=raw_new)

    def _start_hunk(self, match: re.Match[str]) -> None:
        if self._current is None:
            return
        if self._current.current_hunk is not None:
            self._current.hunks.append(self._current.current_hunk.hunk)
            self._current.current_hunk = None
        if len(self._current.hunks) >= MAX_HUNKS_PER_FILE:
            self._current.truncated = True
            self._current.skip_hunks = True
            self._add_redaction("diff truncated after 30 hunks for a file")
            return

        old_start = int(match.group("old_start"))
        old_lines = int(match.group("old_lines") or 1)
        new_start = int(match.group("new_start"))
        new_lines = int(match.group("new_lines") or 1)
        section = match.group("section").strip() or None
        self._current.current_hunk = _CurrentHunkState(
            hunk=SafeDiffHunk(
                old_start=old_start,
                old_lines=old_lines,
                new_start=new_start,
                new_lines=new_lines,
                section_header=section,
                lines=[],
            ),
            old_line_number=old_start,
            new_line_number=new_start,
        )

    def _append_hunk_line(self, raw_line: str) -> None:
        if self._current is None or self._current.current_hunk is None:
            return
        if len(self._current.current_hunk.hunk.lines) >= MAX_LINES_PER_HUNK:
            self._current.truncated = True
            self._current.skip_hunks = True
            self._add_redaction("diff truncated after 200 lines for a hunk")
            return

        if raw_line.startswith("\\ No newline at end of file"):
            return

        kind = "context"
        old_line_number: int | None = self._current.current_hunk.old_line_number
        new_line_number: int | None = self._current.current_hunk.new_line_number
        body = raw_line
        if raw_line.startswith("+") and not raw_line.startswith("+++ "):
            kind = "addition"
            old_line_number = None
            new_line_number = self._current.current_hunk.new_line_number
            self._current.current_hunk.new_line_number += 1
            self._current.additions += 1
            self._total_additions += 1
            body = raw_line[1:]
        elif raw_line.startswith("-") and not raw_line.startswith("--- "):
            kind = "deletion"
            old_line_number = self._current.current_hunk.old_line_number
            new_line_number = None
            self._current.current_hunk.old_line_number += 1
            self._current.deletions += 1
            self._total_deletions += 1
            body = raw_line[1:]
        elif raw_line.startswith(" "):
            old_line_number = self._current.current_hunk.old_line_number
            new_line_number = self._current.current_hunk.new_line_number
            self._current.current_hunk.old_line_number += 1
            self._current.current_hunk.new_line_number += 1
            body = raw_line[1:]
        else:
            return

        sanitized_text, redacted = _sanitize_diff_line_text(body)
        if sanitized_text != body:
            redacted = True
            self._add_redaction("redacted secret-looking value or path in diff line")
        if len(sanitized_text) > MAX_LINE_LENGTH:
            sanitized_text = sanitized_text[: MAX_LINE_LENGTH - len("...[truncated]")] + "...[truncated]"
            redacted = True
            self._add_redaction("diff line truncated to 300 chars")

        self._current.current_hunk.hunk.lines.append(
            SafeDiffLine(
                kind=kind,
                old_line_number=old_line_number,
                new_line_number=new_line_number,
                text=sanitized_text,
                redacted=redacted,
            )
        )

    def _finalize_current_file(self) -> None:
        if self._current is None:
            return

        file_state = self._current
        if file_state.current_hunk is not None:
            file_state.hunks.append(file_state.current_hunk.hunk)
            file_state.current_hunk = None
        path, change_type, path_redacted = _finalize_file_path(file_state)
        if path_redacted:
            self._add_redaction("blocked unsafe path in reviewed diff")

        if file_state.binary:
            change_type = "binary"
            file_state.truncated = True

        hunks = list(file_state.hunks)
        self._files.append(
            SafeDiffFile(
                path=path,
                change_type=change_type,
                additions=file_state.additions,
                deletions=file_state.deletions,
                hunks=hunks,
                truncated=file_state.truncated,
            )
        )
        self._current = None

    def _add_redaction(self, reason: str) -> None:
        if reason not in self._redactions:
            self._redactions.append(reason)


@dataclass
class _CurrentHunkState:
    hunk: SafeDiffHunk
    old_line_number: int
    new_line_number: int


@dataclass
class _CurrentFileState:
    raw_old: str
    raw_new: str
    old_path: str | None = None
    new_path: str | None = None
    rename_from: str | None = None
    rename_to: str | None = None
    binary: bool = False
    truncated: bool = False
    skip_hunks: bool = False
    additions: int = 0
    deletions: int = 0
    hunks: list[SafeDiffHunk] = field(default_factory=list)
    current_hunk: _CurrentHunkState | None = None


def _finalize_file_path(file_state: _CurrentFileState) -> tuple[str, str, bool]:
    old_raw = file_state.old_path or file_state.raw_old
    new_raw = file_state.new_path or file_state.raw_new
    old_display, old_redacted = _sanitize_path(old_raw)
    new_display, new_redacted = _sanitize_path(new_raw)

    if file_state.binary:
        raw_path = file_state.rename_to or file_state.new_path or file_state.raw_new or file_state.raw_old
        path, redacted = _sanitize_path(raw_path)
        return path, "binary", redacted

    if old_raw == "/dev/null" and new_raw and new_raw != "/dev/null":
        if new_redacted:
            return "[redacted-path]", "added", True
        return new_display, "added", False
    if new_raw == "/dev/null" and old_raw and old_raw != "/dev/null":
        if old_redacted:
            return "[redacted-path]", "deleted", True
        return old_display, "deleted", False
    if file_state.rename_from or file_state.rename_to or (
        old_raw and new_raw and _header_compare_value(old_raw) != _header_compare_value(new_raw) and old_raw != "/dev/null" and new_raw != "/dev/null"
    ):
        if old_redacted or new_redacted:
            return "[redacted-path]", "renamed", True
        candidate = file_state.rename_to or new_raw or old_raw
        path, redacted = _sanitize_path(candidate)
        return path, "renamed", redacted
    if old_redacted or new_redacted:
        return "[redacted-path]", "modified", True
    candidate = new_display or old_display or file_state.raw_new or file_state.raw_old
    path, redacted = _sanitize_path(candidate)
    return path, "modified", redacted


def _sanitize_diff_ref(diff_ref: str | Path | None) -> str | None:
    if diff_ref is None:
        return None
    return Path(str(diff_ref)).name


def _safe_diff_ref(diff_ref: str | Path | None) -> str | None:
    return _sanitize_diff_ref(diff_ref)


def _header_compare_value(raw_path: str | None) -> str:
    if raw_path is None:
        return ""
    normalized = str(raw_path).replace("\\", "/").strip()
    if normalized.startswith(("a/", "b/")):
        normalized = normalized[2:]
    return normalized


def _sanitize_diff_line_text(text: str) -> tuple[str, bool]:
    cleaned = _strip_control_chars(text)
    redacted = cleaned != text
    sanitized = redact_patch_preview(cleaned, max_chars=10_000, redact_secrets=True, redact_paths=True)
    sanitized = redact_model_summary(sanitized)
    if sanitized != cleaned:
        redacted = True
    return sanitized, redacted


def _sanitize_path(raw_path: str | None) -> tuple[str, bool]:
    if raw_path is None:
        return "[redacted-path]", True
    original = str(raw_path)
    path = _strip_control_chars(original).strip()
    if not path:
        return "[redacted-path]", True
    control_chars_removed = path != original
    normalized = path.replace("\\", "/")
    if normalized.startswith(("a/", "b/")):
        normalized = normalized[2:]

    workspace_name = Path.cwd().name.lower()
    lowered = normalized.lower()
    first_segment = lowered.split("/", 1)[0]
    unsafe = (
        _WINDOWS_DRIVE_RE.match(normalized) is not None
        or _UNC_PATH_RE.match(normalized) is not None
        or normalized.startswith("/")
        or lowered.startswith("/users/")
        or lowered.startswith("/home/")
        or lowered.startswith("../")
        or "/../" in lowered
        or lowered == ".."
        or first_segment == workspace_name
    )
    if unsafe:
        return "[redacted-path]", True
    if control_chars_removed:
        return "[redacted-path]", True
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized:
        return "[redacted-path]", True
    return Path(normalized).as_posix(), False


def _strip_control_chars(text: str) -> str:
    result: list[str] = []
    for ch in text:
        if ch == "\t":
            result.append(" ")
            continue
        if ord(ch) < 32 or ord(ch) == 127:
            continue
        result.append(ch)
    return "".join(result)
