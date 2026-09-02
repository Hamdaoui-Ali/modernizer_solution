from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from migration_factory.final_report.pdf_writer import (
    write_text_pdf_from_markdown,
    _wrap_text,
    _split_long_word,
)


def test_pdf_writes_valid_pdf_with_content() -> None:
    markdown = """# Test Report

- Run ID: test-123
- Status: completed

## Validated

- sandbox transform applied

POC-ready sandbox migration artifacts.
"""
    with TemporaryDirectory() as tmpdir:
        md_path = Path(tmpdir) / "input.md"
        pdf_path = Path(tmpdir) / "output.pdf"
        md_path.write_text(markdown, encoding="utf-8")

        write_text_pdf_from_markdown(str(md_path), str(pdf_path))

        assert pdf_path.is_file()
        content = pdf_path.read_bytes()
        assert content.startswith(b"%PDF-1.4")
        assert len(content) > 200


def test_pdf_handles_table_with_long_cells() -> None:
    markdown = """# Report

| Header1 | Header2 |
|---------|---------|
| /this/is/a/very/long/unbroken/path/that/should/wrap/automatically | value2 |
"""
    with TemporaryDirectory() as tmpdir:
        md_path = Path(tmpdir) / "input.md"
        pdf_path = Path(tmpdir) / "output.pdf"
        md_path.write_text(markdown, encoding="utf-8")

        write_text_pdf_from_markdown(str(md_path), str(pdf_path))

        assert pdf_path.is_file()
        content = pdf_path.read_bytes()
        assert content.startswith(b"%PDF-1.4")


def test_wrap_text_splits_long_lines() -> None:
    text = "A" * 200
    wrapped = _wrap_text(text, max_chars=80)
    assert len(wrapped) > 1
    assert all(len(line) <= 80 for line in wrapped)


def test_wrap_text_handles_br_tags() -> None:
    text = "line1<br />line2<br>line3"
    wrapped = _wrap_text(text, max_chars=80)
    assert len(wrapped) == 3


def test_split_long_word_at_separator() -> None:
    text = "a/very/long/path/component"
    result = _split_long_word(text, max_chars=10)
    assert result is not None
    part, rest = result
    assert len(part) <= 10
    assert len(part) + len(rest) == len(text)


def test_split_long_word_returns_none_for_short_text() -> None:
    text = "short"
    result = _split_long_word(text, max_chars=80)
    assert result is None
