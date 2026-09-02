"""Target dependency version comparison and safe POM update helpers.

This module is intentionally backend-owned: the browser may provide requested
coordinates and target versions, but only backend-resolved POM content is read
and only targeted XML spans are patched.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

_COORDINATE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]*$")
_TAG_RE_TEMPLATE = r"<(?P<tag>{tag})\b[^>]*>\s*(?P<value>[\s\S]*?)\s*</(?P=tag)>"
_PROPERTY_REF_RE = re.compile(r"^\$\{([^}]+)\}$")


def atomic_replace_text(path: str | os.PathLike[str], content: str) -> None:
    target = os.fspath(path)
    directory = os.path.dirname(target) or "."
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as handle:
            temporary = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


@dataclass(frozen=True)
class PomTargetVersionChange:
    group_id: str
    artifact_id: str
    target_version: str

    @property
    def coordinate(self) -> str:
        return f"{self.group_id}:{self.artifact_id}"


@dataclass(frozen=True)
class _PomEntry:
    group_id: str
    artifact_id: str
    type: str
    classifier: str
    declaration_kind: str
    version_source: str
    raw_version: str | None
    resolved_version: str | None
    version_span: tuple[int, int] | None
    property_name: str | None = None
    property_span: tuple[int, int] | None = None

    @property
    def coordinate(self) -> str:
        return f"{self.group_id}:{self.artifact_id}"


@dataclass(frozen=True)
class _PropertyEntry:
    name: str
    value: str
    span: tuple[int, int]


def inspect_pom_for_coordinate(
    pom_text: str, group_id: str, artifact_id: str,
    type: str = "jar", classifier: str = "",
) -> dict[str, Any] | None:
    """Expose the existing raw-POM parser for deterministic repair enrichment."""
    model = _parse_pom_model(pom_text)
    entries = [entry for entry in model["dependencies"] if (
        entry.group_id == group_id and entry.artifact_id == artifact_id
        and entry.type == (type or "jar") and entry.classifier == (classifier or "")
    )]
    if not entries:
        return None
    if len(entries) > 1:
        return {
            "status": "AMBIGUOUS",
            "group_id": group_id,
            "artifact_id": artifact_id,
            "type": type or "jar",
            "classifier": classifier or "",
            "matching_declarations": len(entries),
            "warnings": ["multiple matching dependency declarations found in raw pom.xml"],
        }
    entry = entries[0]
    consumers = [
        {"group_id": item.group_id, "artifact_id": item.artifact_id, "raw_version": item.raw_version,
         "resolved_version": item.resolved_version, "declaration_kind": item.declaration_kind,
         "version_source": item.version_source, "property_name": item.property_name}
        for item in model["dependencies"] if entry.property_name and item.property_name == entry.property_name
    ]
    warnings = []
    status = "MATCH"
    if (
        entry.declaration_kind in {"PARENT", "BOM", "PROFILE"}
        or entry.version_source in {"UNRESOLVED_PROPERTY", "UNKNOWN"}
        or not entry.raw_version
    ):
        status = "PARTIAL"
        warnings.append("raw pom.xml cannot prove inherited, BOM, profile, or unresolved-property values")
    return {
        "status": status,
        "group_id": entry.group_id, "artifact_id": entry.artifact_id,
        "type": entry.type, "classifier": entry.classifier,
        "raw_version": entry.raw_version, "resolved_version": entry.resolved_version,
        "declaration_kind": entry.declaration_kind, "version_source": entry.version_source,
        "property_name": entry.property_name,
        "property_value": entry.resolved_version if entry.property_name else None,
        "literal_version": not bool(entry.property_name), "known_property_consumers": consumers,
        "warnings": warnings,
    }


def apply_target_version_updates(
    pom_text: str,
    changes: list[PomTargetVersionChange],
) -> dict[str, Any]:
    """Apply safe target-version updates to POM text.

    Only existing direct version spans or existing property value spans are
    patched. Missing dependencies, versionless dependencies, parents, duplicate
    entries, malformed XML, invalid requested versions, and ambiguous edits are
    reported as skipped and do not corrupt the POM.
    """

    before_checksum = _sha256_text(pom_text)
    if not pom_text.strip():
        return _result(pom_text, before_checksum, [], ["pom.xml is empty or missing"])
    if not _is_well_formed_xml(pom_text):
        return _result(pom_text, before_checksum, [], ["pom.xml is malformed; no changes applied"])

    model = _parse_pom_model(pom_text)
    entries_by_coordinate: dict[str, list[_PomEntry]] = {}
    for entry in model["dependencies"]:
        entries_by_coordinate.setdefault(entry.coordinate, []).append(entry)
    parent_by_coordinate = {entry.coordinate: entry for entry in model["parents"]}

    items: list[dict[str, Any]] = []
    edits: dict[tuple[int, int], str] = {}
    blockers: list[str] = []

    seen_requests: set[str] = set()
    for change in changes:
        item = _evaluate_change(change, entries_by_coordinate, parent_by_coordinate, edits, seen_requests)
        items.append(item)

    for item in items:
        if item["status"] == "blocked":
            blockers.append(f"{item['coordinate']}: {item['reason']}")

    after_text = pom_text
    applied_items = [item for item in items if item["status"] == "applied"]
    if applied_items:
        for (start, end), replacement in sorted(edits.items(), reverse=True):
            after_text = after_text[:start] + replacement + after_text[end:]
        if not _is_well_formed_xml(after_text):
            reset_items = [
                {**item, "status": "blocked", "reason": "combined patch produced malformed XML; no changes applied"}
                if item["status"] == "applied" else item
                for item in items
            ]
            return _result(pom_text, before_checksum, reset_items, ["combined patch produced malformed XML; no changes applied"])

    return _result(after_text, before_checksum, items, blockers)


def _evaluate_change(
    change: PomTargetVersionChange,
    entries_by_coordinate: dict[str, list[_PomEntry]],
    parent_by_coordinate: dict[str, _PomEntry],
    edits: dict[tuple[int, int], str],
    seen_requests: set[str],
) -> dict[str, Any]:
    coordinate = change.coordinate
    base = {
        "coordinate": coordinate,
        "group_id": change.group_id,
        "artifact_id": change.artifact_id,
        "target_version": change.target_version,
        "before_version": None,
        "after_version": change.target_version,
        "version_source": "not_found",
        "status": "skipped",
        "reason": "",
    }

    if coordinate in seen_requests:
        return {**base, "status": "blocked", "reason": "duplicate target row in request"}
    seen_requests.add(coordinate)

    if not _valid_coordinate_part(change.group_id) or not _valid_coordinate_part(change.artifact_id):
        return {**base, "status": "blocked", "reason": "invalid dependency coordinate"}
    if not _valid_version(change.target_version):
        return {**base, "status": "blocked", "reason": "invalid target version"}

    entries = entries_by_coordinate.get(coordinate, [])
    if not entries:
        if coordinate in parent_by_coordinate:
            parent = parent_by_coordinate[coordinate]
            return {
                **base,
                "before_version": parent.resolved_version,
                "version_source": "parent",
                "status": "blocked",
                "reason": "parent version updates are not applied by this CSV workflow",
            }
        return {**base, "status": "skipped", "reason": "dependency not found in pom.xml"}

    if len(entries) > 1:
        return {**base, "status": "blocked", "reason": "duplicate dependency entries found in pom.xml"}

    entry = entries[0]
    current = entry.resolved_version
    output_version_source = (
        "property" if entry.version_source in {"PROPERTY", "UNRESOLVED_PROPERTY"}
        else "dependency_management" if entry.declaration_kind == "DEPENDENCY_MANAGEMENT"
        else "dependency"
    )
    enriched = {**base, "before_version": current, "version_source": output_version_source}
    if not entry.version_span:
        return {**enriched, "status": "skipped", "reason": "dependency has no explicit version to update"}
    if current and _normalize_version(current) == _normalize_version(change.target_version):
        return {**enriched, "status": "noop", "reason": "target version already present"}

    edit_span = entry.property_span if entry.version_source == "PROPERTY" else entry.version_span
    if edit_span is None:
        return {**enriched, "status": "blocked", "reason": "property version reference could not be resolved"}

    previous = edits.get(edit_span)
    if previous is not None and previous != change.target_version:
        return {**enriched, "status": "blocked", "reason": "multiple target rows require conflicting edits"}
    edits[edit_span] = change.target_version
    return {**enriched, "status": "applied", "reason": "version span updated"}


def _parse_pom_model(pom_text: str) -> dict[str, list[_PomEntry]]:
    properties = _parse_properties(pom_text)
    dependency_management_ranges = _block_ranges(pom_text, "dependencyManagement")
    profile_ranges = _block_ranges(pom_text, "profile")
    dependencies: list[_PomEntry] = []
    for start, end, block in _iter_blocks(pom_text, "dependency"):
        group_id = _extract_tag_text(block, "groupId")
        artifact_id = _extract_tag_text(block, "artifactId")
        if not group_id or not artifact_id:
            continue
        raw_version, local_span = _extract_tag_value_span(block, "version")
        version_span = (start + local_span[0], start + local_span[1]) if local_span else None
        declaration_kind = (
            "DEPENDENCY_MANAGEMENT"
            if _inside_any_range(start, end, dependency_management_ranges)
            else "DIRECT_DEPENDENCY"
        )
        if _inside_any_range(start, end, profile_ranges):
            declaration_kind = "PROFILE"
        if (
            declaration_kind == "DEPENDENCY_MANAGEMENT"
            and _extract_tag_text(block, "type") == "pom"
            and _extract_tag_text(block, "scope") == "import"
        ):
            declaration_kind = "BOM"
        resolved = raw_version
        property_name = None
        property_span = None
        version_source = "LITERAL" if raw_version else "UNKNOWN"
        if raw_version:
            property_match = _PROPERTY_REF_RE.match(raw_version.strip())
            if property_match:
                property_name = property_match.group(1)
                prop = properties.get(property_name)
                version_source = "PROPERTY" if prop else "UNRESOLVED_PROPERTY"
                if prop:
                    resolved = prop.value
                    property_span = prop.span
        dependencies.append(_PomEntry(
            group_id=group_id,
            artifact_id=artifact_id,
            type=_extract_tag_text(block, "type") or "jar",
            classifier=_extract_tag_text(block, "classifier") or "",
            declaration_kind=declaration_kind,
            version_source=version_source,
            raw_version=raw_version,
            resolved_version=resolved,
            version_span=version_span,
            property_name=property_name,
            property_span=property_span,
        ))

    parents: list[_PomEntry] = []
    for start, _, block in _iter_blocks(pom_text, "parent"):
        group_id = _extract_tag_text(block, "groupId")
        artifact_id = _extract_tag_text(block, "artifactId")
        raw_version, local_span = _extract_tag_value_span(block, "version")
        if group_id and artifact_id:
            parents.append(_PomEntry(
                group_id=group_id,
                artifact_id=artifact_id,
                type="jar",
                classifier="",
                declaration_kind="PARENT",
                version_source="PARENT",
                raw_version=raw_version,
                resolved_version=raw_version,
                version_span=(start + local_span[0], start + local_span[1]) if local_span else None,
            ))
    return {"dependencies": dependencies, "parents": parents}


def _parse_properties(pom_text: str) -> dict[str, _PropertyEntry]:
    blocks = list(_iter_blocks(pom_text, "properties"))
    if not blocks:
        return {}
    start, _, block = blocks[0]
    properties: dict[str, _PropertyEntry] = {}
    pattern = re.compile(r"<(?P<name>[A-Za-z0-9_.-]+)\b[^>]*>(?P<value>[^<]*)</(?P=name)>")
    for match in pattern.finditer(block):
        value = _decode_xml_text(match.group("value").strip())
        properties[match.group("name")] = _PropertyEntry(
            name=match.group("name"),
            value=value,
            span=(start + match.start("value"), start + match.end("value")),
        )
    return properties


def _iter_blocks(xml_text: str, tag_name: str) -> list[tuple[int, int, str]]:
    pattern = re.compile(rf"<{re.escape(tag_name)}\b[^>]*>[\s\S]*?</{re.escape(tag_name)}>")
    return [(match.start(), match.end(), match.group(0)) for match in pattern.finditer(xml_text)]


def _block_ranges(xml_text: str, tag_name: str) -> list[tuple[int, int]]:
    return [(start, end) for start, end, _ in _iter_blocks(xml_text, tag_name)]


def _inside_any_range(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start >= range_start and end <= range_end for range_start, range_end in ranges)


def _extract_tag_text(block: str, tag_name: str) -> str | None:
    value, _span = _extract_tag_value_span(block, tag_name)
    return value


def _extract_tag_value_span(block: str, tag_name: str) -> tuple[str | None, tuple[int, int] | None]:
    pattern = re.compile(_TAG_RE_TEMPLATE.format(tag=re.escape(tag_name)))
    match = pattern.search(block)
    if not match:
        return None, None
    return _decode_xml_text(match.group("value").strip()), match.span("value")


def _valid_coordinate_part(value: str) -> bool:
    return bool(value and _COORDINATE_RE.fullmatch(value))


def _valid_version(value: str) -> bool:
    return bool(value and _VERSION_RE.fullmatch(value))


def _normalize_version(value: str) -> str:
    return value.strip().lower()


def _is_well_formed_xml(value: str) -> bool:
    try:
        ET.fromstring(value.encode("utf-8"))
    except ET.ParseError:
        return False
    return True


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decode_xml_text(value: str) -> str:
    return (
        value.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )


def _result(pom_text: str, before_checksum: str, items: list[dict[str, Any]], blockers: list[str]) -> dict[str, Any]:
    return {
        "pom_content": pom_text,
        "before_checksum": before_checksum,
        "after_checksum": _sha256_text(pom_text),
        "applied_count": sum(1 for item in items if item["status"] == "applied"),
        "skipped_count": sum(1 for item in items if item["status"] in {"skipped", "noop"}),
        "blocked_count": sum(1 for item in items if item["status"] == "blocked"),
        "items": items,
        "blockers": blockers,
    }
