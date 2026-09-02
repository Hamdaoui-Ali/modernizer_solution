"""Focused tests for safe reviewed diff preview parsing and sanitization."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from migration_factory.control_tower.application.safe_diff_preview import (
    build_safe_diff_preview,
)


def _write_diff(tmp_path: Path, name: str, diff_text: str) -> Path:
    diff_path = tmp_path / name
    diff_path.write_text(diff_text, encoding="utf-8")
    return diff_path


def test_parses_basic_modified_file_with_context_addition_and_deletion(tmp_path: Path) -> None:
    diff_path = _write_diff(
        tmp_path,
        "final_reviewed_repair.diff",
        "\n".join(
            [
                "diff --git a/src/app.txt b/src/app.txt",
                "--- a/src/app.txt",
                "+++ b/src/app.txt",
                "@@ -1,3 +1,4 @@ section",
                " line one",
                "-line two",
                "+line two updated",
                " line three",
            ]
        )
        + "\n",
    )

    preview = build_safe_diff_preview(
        proposal_id="proposal-1",
        diff_ref=diff_path,
    )

    assert preview.proposal_id == "proposal-1"
    assert preview.diff_ref == "final_reviewed_repair.diff"
    assert len(preview.files) == 1
    file = preview.files[0]
    assert file.path == "src/app.txt"
    assert file.change_type == "modified"
    assert file.additions == 1
    assert file.deletions == 1
    assert preview.total_additions == 1
    assert preview.total_deletions == 1
    assert file.hunks[0].old_start == 1
    assert file.hunks[0].new_start == 1
    assert file.hunks[0].lines[0].old_line_number == 1
    assert file.hunks[0].lines[0].new_line_number == 1
    assert file.hunks[0].lines[1].old_line_number == 2
    assert file.hunks[0].lines[1].new_line_number is None
    assert file.hunks[0].lines[2].old_line_number is None
    assert file.hunks[0].lines[2].new_line_number == 2
    assert file.hunks[0].lines[3].old_line_number == 3
    assert file.hunks[0].lines[3].new_line_number == 3


def test_parses_added_deleted_and_renamed_files(tmp_path: Path) -> None:
    diff_path = _write_diff(
        tmp_path,
        "multi.diff",
        "\n".join(
            [
                "diff --git a/new.txt b/new.txt",
                "--- /dev/null",
                "+++ b/new.txt",
                "@@ -0,0 +1,2 @@",
                "+added one",
                "+added two",
                "diff --git a/old.txt b/old.txt",
                "--- a/old.txt",
                "+++ /dev/null",
                "@@ -1,2 +0,0 @@",
                "-removed one",
                "-removed two",
                "diff --git a/old-name.txt b/new-name.txt",
                "rename from old-name.txt",
                "rename to new-name.txt",
                "@@ -1,1 +1,1 @@",
                "-old",
                "+new",
            ]
        )
        + "\n",
    )

    preview = build_safe_diff_preview(proposal_id="proposal-2", diff_ref=diff_path)

    assert [file.change_type for file in preview.files] == ["added", "deleted", "renamed"]
    assert [file.path for file in preview.files] == ["new.txt", "old.txt", "new-name.txt"]
    assert preview.files[0].additions == 2
    assert preview.files[1].deletions == 2
    assert preview.files[2].additions == 1
    assert preview.files[2].deletions == 1


def test_handles_binary_diff_safely(tmp_path: Path) -> None:
    diff_path = _write_diff(
        tmp_path,
        "binary.diff",
        "\n".join(
            [
                "diff --git a/assets/logo.png b/assets/logo.png",
                "Binary files a/assets/logo.png and b/assets/logo.png differ",
            ]
        )
        + "\n",
    )

    preview = build_safe_diff_preview(proposal_id="proposal-3", diff_ref=diff_path)

    assert preview.files[0].change_type == "binary"
    assert preview.files[0].hunks == []
    assert preview.files[0].truncated is True
    assert "binary diff encountered" in preview.redactions


@pytest.mark.parametrize(
    ("path_line", "expected_reason"),
    [
        ("--- a//Users/alice/secret.txt", "blocked unsafe path in reviewed diff"),
        ("--- a/C:\\Users\\alice\\secret.txt", "blocked unsafe path in reviewed diff"),
        ("--- a/\\\\server\\share\\secret.txt", "blocked unsafe path in reviewed diff"),
        ("--- a//home/alice/secret.txt", "blocked unsafe path in reviewed diff"),
        ("--- a/../secret.txt", "blocked unsafe path in reviewed diff"),
        ("--- a/src/\x00secret.txt", "blocked unsafe path in reviewed diff"),
        ("--- a/modernizer-solution/src/secret.txt", "blocked unsafe path in reviewed diff"),
    ],
)
def test_rejects_or_redacts_unsafe_paths(tmp_path: Path, path_line: str, expected_reason: str) -> None:
    diff_path = _write_diff(
        tmp_path,
        "unsafe.diff",
        "\n".join(
            [
                "diff --git a/unsafe.txt b/unsafe.txt",
                path_line,
                "+++ b/unsafe.txt",
                "@@ -1,1 +1,1 @@",
                "-old",
                "+new",
            ]
        )
        + "\n",
    )

    preview = build_safe_diff_preview(proposal_id="proposal-4", diff_ref=diff_path)

    assert preview.files[0].path == "[redacted-path]"
    assert expected_reason in preview.redactions


def test_redacts_secret_like_values(tmp_path: Path) -> None:
    diff_path = _write_diff(
        tmp_path,
        "secrets.diff",
        "\n".join(
            [
                "diff --git a/src/config.yml b/src/config.yml",
                "--- a/src/config.yml",
                "+++ b/src/config.yml",
                "@@ -1,4 +1,4 @@",
                "-api_key=abc123",
                "+password=super-secret",
                "-Authorization: Bearer token-abc123",
                "+AZURE_OPENAI_API_KEY=azure-secret",
            ]
        )
        + "\n",
    )

    preview = build_safe_diff_preview(proposal_id="proposal-5", diff_ref=diff_path)
    texts = [line.text for line in preview.files[0].hunks[0].lines]

    assert all("abc123" not in text for text in texts)
    assert all("super-secret" not in text for text in texts)
    assert all("Bearer token-abc123" not in text for text in texts)
    assert all("azure-secret" not in text for text in texts)
    assert any(line.redacted for line in preview.files[0].hunks[0].lines)


def test_truncates_large_diff_and_sets_truncated(tmp_path: Path) -> None:
    large_lines = [
        "diff --git a/src/big.txt b/src/big.txt",
        "--- a/src/big.txt",
        "+++ b/src/big.txt",
        "@@ -1,1 +1,1 @@",
    ]
    large_lines.extend(f"+line {i:05d} " + ("x" * 60) for i in range(7000))
    diff_path = _write_diff(tmp_path, "large.diff", "\n".join(large_lines) + "\n")

    preview = build_safe_diff_preview(proposal_id="proposal-6", diff_ref=diff_path)

    assert preview.truncated is True
    assert preview.files[0].truncated is True
    assert preview.total_additions > 0
    assert "diff truncated to 200KB preview window" in preview.redactions


def test_checksum_is_content_derived_and_changes_when_diff_changes(tmp_path: Path) -> None:
    diff_one = _write_diff(
        tmp_path,
        "one.diff",
        "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,1 +1,1 @@\n-old\n+new\n",
    )
    diff_two = _write_diff(
        tmp_path,
        "two.diff",
        "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,1 +1,1 @@\n-old\n+different\n",
    )

    preview_one = build_safe_diff_preview(proposal_id="proposal-7", diff_ref=diff_one)
    preview_two = build_safe_diff_preview(proposal_id="proposal-7", diff_ref=diff_two)

    assert preview_one.diff_checksum != preview_two.diff_checksum
    assert preview_one.diff_checksum == hashlib.sha256(diff_one.read_bytes()).hexdigest()
