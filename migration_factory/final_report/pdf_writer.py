from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_PAGE_WIDTH = 595
_PAGE_HEIGHT = 842
_MARGIN = 50
_CONTENT_WIDTH = _PAGE_WIDTH - 2 * _MARGIN
_FONT_SIZE = 10
_HEADING_SIZES = {1: 22, 2: 16, 3: 13, 4: 11, 5: 10}
_LINE_HEIGHT = 14
_TABLE_LINE_HEIGHT = 20
_MAX_CELL_CHARS = 80


@dataclass
class _PdfCanvas:
    objects: list[dict[str, Any]] = field(default_factory=list)
    y: float = _PAGE_HEIGHT - _MARGIN
    page: int = 1
    font_name: str = "Helvetica"
    font_size: int = _FONT_SIZE
    content: list[str] = field(default_factory=list)
    object_index: int = 0

    def add_page(self) -> None:
        self.page += 1
        self.y = _PAGE_HEIGHT - _MARGIN
        self._flush_page()

    def _flush_page(self) -> None:
        pass

    def write_text(
        self,
        text: str,
        size: int | None = None,
        bold: bool = False,
        indent: float = 0,
    ) -> None:
        if self.y < _MARGIN + _LINE_HEIGHT:
            self.add_page()
        self.content.append(
            f"BT "
            f"/F{'B' if bold else 'H'} {size or _FONT_SIZE} Tf "
            f"{_MARGIN + indent:.0f} {self.y:.0f} Td "
            f"({_escape_pdf(text)}) Tj ET"
        )
        self.y -= _LINE_HEIGHT + (4 if bold and (size or _FONT_SIZE) > 12 else 0)

    def write_wrapped(
        self,
        text: str,
        size: int | None = None,
        bold: bool = False,
        indent: float = 0,
        max_chars: int = _MAX_CELL_CHARS,
    ) -> None:
        wrapped = _wrap_text(text, max_chars)
        for line in wrapped:
            self.write_text(line, size=size, bold=bold, indent=indent)

    def draw_table(
        self,
        headers: list[str],
        rows: list[list[str]],
        col_widths: list[float] | None = None,
    ) -> None:
        if not rows:
            return
        if col_widths is None:
            col_widths = [_CONTENT_WIDTH / len(headers)] * len(headers)

        full_table_height = _table_height(rows, col_widths)
        if self.y - full_table_height < _MARGIN:
            self.add_page()

        row_height = _TABLE_LINE_HEIGHT
        x_start = _MARGIN
        y_start = self.y

        for header, width in zip(headers, col_widths):
            x_end = x_start + width
            self.content.append(
                f"BT /FB {_FONT_SIZE} Tf {x_start:.0f} {y_start - 2:.0f} Td "
                f"({_escape_pdf(header)}) Tj ET"
            )
            self.content.append(
                f"{x_start:.0f} {y_start:.0f} {width:.0f} {row_height:.0f} re S"
            )
            x_start = x_end

        self.y -= row_height

        for row in rows:
            max_lines = max(len(_wrap_text(str(cell or ""), _MAX_CELL_CHARS)) for cell in row)
            cell_row_height = max(row_height, max_lines * _LINE_HEIGHT)

            if self.y - cell_row_height < _MARGIN:
                self.add_page()
                y_start = self.y

            x_start = _MARGIN
            for cell, width in zip(row, col_widths):
                x_end = x_start + width
                wrapped = _wrap_text(str(cell or ""), int(width / 5.5))
                cell_y = self.y
                for line in wrapped:
                    if cell_y < _MARGIN + _LINE_HEIGHT:
                        break
                    self.content.append(
                        f"BT /FH {_FONT_SIZE - 1} Tf "
                        f"{x_start + 2:.0f} {cell_y - 2:.0f} Td "
                        f"({_escape_pdf(line)}) Tj ET"
                    )
                    cell_y -= _LINE_HEIGHT
                self.content.append(
                    f"{x_start:.0f} {self.y - cell_row_height:.0f} "
                    f"{width:.0f} {cell_row_height:.0f} re S"
                )
                x_start = x_end

            self.y -= cell_row_height


def write_text_pdf_from_markdown(markdown_path: str | Path, output_pdf_path: str | Path) -> None:
    markdown_path = Path(markdown_path)
    output_pdf_path = Path(output_pdf_path)
    output_pdf_path.parent.mkdir(parents=True, exist_ok=True)

    text = markdown_path.read_text(encoding="utf-8")
    parsed = _parse_markdown(text)

    canvas = _PdfCanvas()
    _render_document(canvas, parsed)

    pdf_bytes = _build_pdf_bytes(canvas, text)
    output_pdf_path.write_bytes(pdf_bytes)


def _parse_markdown(text: str) -> list[dict[str, Any]]:
    lines = text.split("\n")
    blocks: list[dict[str, Any]] = []
    in_table = False
    table_headers: list[str] = []
    table_rows: list[list[str]] = []
    table_col_widths: list[float] | None = None

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [
                c.strip()
                for c in stripped.strip("|").split("|")
            ]
            if not in_table:
                in_table = True
                table_headers = cells
                table_rows = []
            elif re.match(r"^[\s|:\-]+$", stripped):
                col_count = len(cells)
                table_col_widths = [_CONTENT_WIDTH / max(col_count, 1)] * max(col_count, 1)
            else:
                table_rows.append(cells)
            continue

        if in_table and table_headers:
            blocks.append({
                "type": "table",
                "headers": table_headers,
                "rows": table_rows,
                "col_widths": table_col_widths,
            })
            in_table = False
            table_headers = []
            table_rows = []
            table_col_widths = None

        if not stripped:
            blocks.append({"type": "spacer"})
            continue

        heading = re.match(r"^(#{1,5})\s+(.+)$", stripped)
        if heading:
            level = len(heading.group(1))
            blocks.append({
                "type": "heading",
                "level": level,
                "text": heading.group(2).strip(),
            })
            continue

        if stripped.startswith("- ") or stripped.startswith("* "):
            blocks.append({
                "type": "bullet",
                "text": stripped[2:].strip(),
            })
            continue

        blocks.append({
            "type": "paragraph",
            "text": stripped,
        })

    if in_table and table_headers:
        blocks.append({
            "type": "table",
            "headers": table_headers,
            "rows": table_rows,
            "col_widths": table_col_widths,
        })

    return blocks


def _render_document(canvas: _PdfCanvas, blocks: list[dict[str, Any]]) -> None:
    canvas.write_text(
        "Migration Report",
        size=22,
        bold=True,
    )
    canvas.y -= 6

    for block in blocks:
        block_type = block.get("type", "")

        if block_type == "spacer":
            canvas.y -= 6
            continue

        if block_type == "heading":
            level = block.get("level", 1)
            size = _HEADING_SIZES.get(level, 10)
            canvas.write_text(block.get("text", ""), size=size, bold=True)
            continue

        if block_type == "paragraph":
            text = block.get("text", "")
            if text.startswith("- ") or text.startswith("* "):
                canvas.write_wrapped(text, indent=10)
            else:
                canvas.write_wrapped(text)
            continue

        if block_type == "bullet":
            canvas.write_wrapped(f"• {block.get('text', '')}", indent=10)
            continue

        if block_type == "table":
            headers = block.get("headers", [])
            rows = block.get("rows", [])
            col_widths = block.get("col_widths")
            canvas.draw_table(headers, rows, col_widths=col_widths)
            continue


def _build_pdf_bytes(canvas: _PdfCanvas, text: str) -> bytes:
    pages = _split_into_pages(canvas.content)
    objects: list[bytes] = []
    object_refs: list[int] = []

    obj_num = 0

    def _next_obj(data: bytes) -> int:
        nonlocal obj_num
        obj_num += 1
        objects.append(data)
        return obj_num

    page_object_numbers: list[int] = []
    for page_content in pages:
        content_stream = b"\n".join(
            line.encode("latin-1", errors="replace") if isinstance(line, str) else line
            for line in page_content
        )
        content_stream = b"q\n" + content_stream + b"\nQ\n"
        content_obj = _next_obj(
            b"<< /Length " + str(len(content_stream)).encode() + b" >>\nstream\n" + content_stream + b"\nendstream"
        )

        font_helv = _next_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        font_helv_bold = _next_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")

        resources = _next_obj(
            b"<< /Font <</FH " + str(font_helv).encode() + b" 0 R /FB " + str(font_helv_bold).encode() + b" 0 R>> >>"
        )

        page_obj = _next_obj(
            b"<< /Type /Page /Parent 3 0 R /MediaBox [0 0 "
            + str(_PAGE_WIDTH).encode() + b" " + str(_PAGE_HEIGHT).encode()
            + b"] /Contents " + str(content_obj).encode() + b" 0 R /Resources " + str(resources).encode() + b" 0 R >>"
        )
        page_object_numbers.append(page_obj)

    pages_obj = _next_obj(
        b"<< /Type /Pages /Kids ["
        + b" ".join(str(p).encode() + b" 0 R" for p in page_object_numbers)
        + b"] /Count " + str(len(page_object_numbers)).encode() + b" >>"
    )

    header = b"%PDF-1.4\n"

    body = b""
    for i, data in enumerate(objects, start=1):
        body += f"{i} 0 obj\n".encode() + data + b"\nendobj\n"

    xref_offset = len(header + body)
    xref = b"xref\n0 " + str(obj_num + 1).encode() + b"\n"
    xref += b"0000000000 65535 f \n"
    offset = 0
    for i in range(obj_num):
        if i == 0:
            offset = len(header)
        xref += f"{offset:010d} 00000 n \n".encode()
        offset += len(objects[i - 1]) + len(f"{i} 0 obj\n".encode()) + len(b"\nendobj\n") if i > 0 else 0

    trailer = b"trailer\n<< /Size " + str(obj_num + 1).encode() + b" /Root 1 0 R >>\n"
    # Override page tree reference — we need pages_obj to be the last object
    # Rebuild: the first object is always the catalog referencing page tree
    # For simplicity, use a fixed approach
    # Actually, let's rebuild properly.

    # Reset and build simply
    objects2: list[bytes] = []

    font_h = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    font_b = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"
    resources2 = b"<< /Font <</FH 2 0 R /FB 3 0 R>> >>"

    page_content_list: list[int] = []
    page_refs: list[int] = []

    next_n = 4
    for p_idx, page_content in enumerate(pages):
        pc_stream = b"q\n" + b"\n".join(
            line.encode("latin-1", errors="replace") if isinstance(line, str) else line
            for line in page_content
        ) + b"\nQ\n"
        pc_obj = b"<< /Length " + str(len(pc_stream)).encode() + b" >>\nstream\n" + pc_stream + b"\nendstream"
        objects2.append(pc_obj)
        page_content_list.append(next_n)
        next_n += 1

    for p_idx in range(len(pages)):
        p_obj = (
            b"<< /Type /Page /Parent 1 0 R /MediaBox [0 0 "
            + str(_PAGE_WIDTH).encode() + b" " + str(_PAGE_HEIGHT).encode()
            + b"] /Contents " + str(page_content_list[p_idx]).encode() + b" 0 R /Resources " + str(next_n).encode() + b" 0 R >>"
        )
        objects2.append(p_obj)
        page_refs.append(next_n)
        next_n += 1

    pages_obj2 = (
        b"<< /Type /Pages /Kids ["
        + b" ".join(str(r).encode() + b" 0 R" for r in page_refs)
        + b"] /Count " + str(len(page_refs)).encode() + b" >>"
    )
    objects2.append(pages_obj2)  # will be object 1
    # Actually index 0 will be catalog - let me redo this properly.

    # Simplest approach: fixed object layout
    # 1: Pages (parent)
    # 2: Font H
    # 3: Font B
    # 4..N: Content streams
    # N+1..: Page objects
    # Last: Resources (shareable)

    all_objects: list[bytes] = []
    all_objects.append(pages_obj2)  # becomes object 1
    all_objects.append(font_h)       # becomes object 2
    all_objects.append(font_b)       # becomes object 3
    content_start = 4
    content_objs: list[int] = []
    for p_idx, page_content in enumerate(pages):
        pc_stream = b"q\n" + b"\n".join(
            line.encode("latin-1", errors="replace") if isinstance(line, str) else line
            for line in page_content
        ) + b"\nQ\n"
        pc_obj = b"<< /Length " + str(len(pc_stream)).encode() + b" >>\nstream\n" + pc_stream + b"\nendstream"
        all_objects.append(pc_obj)
        content_objs.append(content_start + p_idx)

    resources_num = content_start + len(pages)
    all_objects.append(resources2)  # shareable resources

    page_nums: list[int] = []
    for p_idx in range(len(pages)):
        p_obj = (
            b"<< /Type /Page /Parent 1 0 R /MediaBox [0 0 "
            + str(_PAGE_WIDTH).encode() + b" " + str(_PAGE_HEIGHT).encode()
            + b"] /Contents " + str(content_objs[p_idx]).encode() + b" 0 R /Resources " + str(resources_num).encode() + b" 0 R >>"
        )
        all_objects.append(p_obj)
        page_nums.append(resources_num + 1 + p_idx)

    catalog = b"<< /Type /Catalog /Pages 1 0 R >>"
    all_objects.insert(0, catalog)  # becomes object 0? No, shift everything.
    # Let's redo: all_objects[0] = catalog, all_objects[1] = pages, etc.
    all_objects_final: list[bytes] = [
        catalog,  # 1 0 obj
        pages_obj2,  # 2 0 obj
        font_h,  # 3 0 obj
        font_b,  # 4 0 obj
    ]
    content_start_final = 5
    content_objs_final: list[int] = []
    for p_idx, page_content in enumerate(pages):
        pc_stream = b"q\n" + b"\n".join(
            line.encode("latin-1", errors="replace") if isinstance(line, str) else line
            for line in page_content
        ) + b"\nQ\n"
        pc_obj = b"<< /Length " + str(len(pc_stream)).encode() + b" >>\nstream\n" + pc_stream + b"\nendstream"
        all_objects_final.append(pc_obj)
        content_objs_final.append(content_start_final + p_idx)

    resources_num_final = content_start_final + len(pages)
    all_objects_final.append(resources2)

    for p_idx in range(len(pages)):
        p_obj = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 "
            + str(_PAGE_WIDTH).encode() + b" " + str(_PAGE_HEIGHT).encode()
            + b"] /Contents " + str(content_objs_final[p_idx]).encode() + b" 0 R /Resources "
            + str(resources_num_final).encode() + b" 0 R >>"
        )
        all_objects_final.append(p_obj)

    body2 = b""
    for i, data in enumerate(all_objects_final, start=1):
        body2 += f"{i} 0 obj\n".encode() + data + b"\nendobj\n"

    xref_offset2 = len(header + body2)
    num_objects = len(all_objects_final)
    xref2 = b"xref\n0 " + str(num_objects + 1).encode() + b"\n"
    xref2 += b"0000000000 65535 f \n"
    pos = len(header)
    for i in range(1, num_objects + 1):
        xref2 += f"{pos:010d} 00000 n \n".encode()
        obj_data = all_objects_final[i - 1]
        obj_entry = f"{i} 0 obj\n".encode() + obj_data + b"\nendobj\n"
        pos += len(obj_entry)

    trailer2 = b"trailer\n<< /Size " + str(num_objects + 1).encode() + b" /Root 1 0 R >>\n"
    eof = b"startxref\n" + str(xref_offset2).encode() + b"\n%%EOF\n"

    return header + body2 + xref2 + trailer2 + eof


def _split_into_pages(content: list[str]) -> list[list[str]]:
    if not content:
        return [[]]
    return [content]


def _escape_pdf(text: str) -> str:
    text = str(text)
    text = text.replace("\\", "\\\\")
    text = text.replace("(", "\\(")
    text = text.replace(")", "\\)")
    text = text.replace("\n", "\\n")
    text = text.replace("\r", "\\r")
    text = text.replace("\t", "\\t")
    return text


def _wrap_text(text: str, max_chars: int = _MAX_CELL_CHARS) -> list[str]:
    text = str(text)
    text = text.replace("<br />", "\n").replace("<br>", "\n")
    if len(text) <= max_chars and "\n" not in text:
        return [text]

    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        while len(paragraph) > max_chars:
            split = _split_long_word(paragraph, max_chars)
            if split:
                part, paragraph = split
            else:
                part = paragraph[:max_chars]
                paragraph = paragraph[max_chars:]
            lines.append(part)
        if paragraph:
            lines.append(paragraph)

    return lines if lines else [""]


def _split_long_word(text: str, max_chars: int) -> tuple[str, str] | None:
    if len(text) <= max_chars:
        return None
    for sep in ("/", "\\", ".", "-", "_", ":", " "):
        idx = text.rfind(sep, 0, max_chars)
        if idx > 0:
            return text[:idx + 1], text[idx + 1:]
    return None


def _table_height(rows: list[list[str]], col_widths: list[float]) -> float:
    if not rows:
        return 0
    height = _TABLE_LINE_HEIGHT
    for row in rows:
        max_lines = 1
        for cell in row:
            wrapped = _wrap_text(str(cell or ""), int(max(col_widths[0], 1) / 5.5)) if col_widths else [str(cell or "")]
            max_lines = max(max_lines, len(wrapped))
        height += max(max(_TABLE_LINE_HEIGHT, max_lines * _LINE_HEIGHT), _TABLE_LINE_HEIGHT)
    return height
