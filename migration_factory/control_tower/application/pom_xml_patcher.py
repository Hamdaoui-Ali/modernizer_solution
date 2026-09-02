"""F14 — Formatting-preserving POM XML patch engine.

Uses targeted text/span edits to modify POM files without
destroying formatting, comments, or namespace prefixes.

Never uses xml.etree.ElementTree for serialization.
"""

from __future__ import annotations

import difflib
import hashlib
import re as _re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PomPatchResult:
    """Result of a POM patch operation."""
    success: bool
    before_content: str
    after_content: str
    before_checksum: str
    after_checksum: str
    diff_unified: str
    operation: str
    target_desc: str
    before_version: str
    after_version: str
    error: str | None = None


class PomXmlPatcher:
    """Formatting-preserving POM XML patcher.

    Uses targeted regex/string replacements anchored to known
    XML element patterns, not full XML serialization.

    After patching, verifies XML well-formedness by re-parsing.
    """

    # ── Public API ──────────────────────────────────────────────────

    def patch(
        self,
        *,
        pom_content: str,
        operation: str,
        target_kind: str,
        group_id: str | None = None,
        artifact_id: str | None = None,
        property_name: str | None = None,
        plugin_group_id: str | None = None,
        plugin_artifact_id: str | None = None,
        requested_version: str,
    ) -> PomPatchResult:
        """Apply a patch operation to POM content.

        Returns the result with before/after content, checksums, and diff.
        Does NOT write to disk — the caller is responsible for that.
        """

        before_checksum = _sha256(pom_content)

        try:
            after_content = self._apply_operation(
                pom_content=pom_content,
                operation=operation,
                target_kind=target_kind,
                group_id=group_id,
                artifact_id=artifact_id,
                property_name=property_name,
                plugin_group_id=plugin_group_id,
                plugin_artifact_id=plugin_artifact_id,
                requested_version=requested_version,
            )
        except PomPatchError as e:
            return PomPatchResult(
                success=False,
                before_content=pom_content,
                after_content=pom_content,
                before_checksum=before_checksum,
                after_checksum=before_checksum,
                diff_unified="",
                operation=operation,
                target_desc=_target_desc(target_kind, group_id, artifact_id, property_name),
                before_version="",
                after_version=requested_version,
                error=str(e),
            )

        # Verify XML well-formedness after patch
        if not _is_well_formed_xml(after_content):
            return PomPatchResult(
                success=False,
                before_content=pom_content,
                after_content=pom_content,
                before_checksum=before_checksum,
                after_checksum=before_checksum,
                diff_unified="",
                operation=operation,
                target_desc=_target_desc(target_kind, group_id, artifact_id, property_name),
                before_version="",
                after_version=requested_version,
                error="Patch produced malformed XML. Change not applied.",
            )

        after_checksum = _sha256(after_content)
        diff = _unified_diff(pom_content, after_content)

        # Extract before version
        before_version = self._extract_current_version(
            pom_content=pom_content,
            target_kind=target_kind,
            group_id=group_id,
            artifact_id=artifact_id,
            property_name=property_name,
            plugin_group_id=plugin_group_id,
            plugin_artifact_id=plugin_artifact_id,
        )

        return PomPatchResult(
            success=True,
            before_content=pom_content,
            after_content=after_content,
            before_checksum=before_checksum,
            after_checksum=after_checksum,
            diff_unified=diff,
            operation=operation,
            target_desc=_target_desc(target_kind, group_id, artifact_id, property_name),
            before_version=before_version,
            after_version=requested_version,
        )

    def extract_current_version(
        self,
        pom_content: str,
        target_kind: str,
        group_id: str | None = None,
        artifact_id: str | None = None,
        property_name: str | None = None,
        plugin_group_id: str | None = None,
        plugin_artifact_id: str | None = None,
    ) -> str:
        """Extract the current version of a target from POM content."""
        return self._extract_current_version(
            pom_content=pom_content,
            target_kind=target_kind,
            group_id=group_id,
            artifact_id=artifact_id,
            property_name=property_name,
            plugin_group_id=plugin_group_id,
            plugin_artifact_id=plugin_artifact_id,
        )

    # ── Operation dispatch ──────────────────────────────────────────

    def _apply_operation(
        self,
        pom_content: str,
        operation: str,
        **kwargs,
    ) -> str:
        handlers = {
            "update_property_version": self._patch_property_version,
            "update_dependency_version": self._patch_dependency_version,
            "remove_dependency_version": self._patch_remove_dependency_version,
            "update_plugin_version": self._patch_plugin_version,
        }

        handler = handlers.get(operation)
        if handler is None:
            raise PomPatchError(f"Unsupported operation: {operation}")
        return handler(pom_content, **kwargs)

    # ── Property version patch ──────────────────────────────────────

    def _patch_property_version(
        self,
        pom_content: str,
        property_name: str | None,
        requested_version: str,
        **__,
    ) -> str:
        if not property_name:
            raise PomPatchError("property_name is required for update_property_version")

        # Find <properties> block and the specific property
        # Match: <property.name>VALUE</property.name>
        pattern = _re.compile(
            rf"(<{_re.escape(property_name)}>)([^<]*)(</{_re.escape(property_name)}>)"
        )
        match = pattern.search(pom_content)
        if not match:
            raise PomPatchError(f"Property '{property_name}' not found in POM")

        return pom_content[:match.start(2)] + requested_version + pom_content[match.end(2):]

    # ── Dependency version patch ────────────────────────────────────

    def _patch_dependency_version(
        self,
        pom_content: str,
        group_id: str | None,
        artifact_id: str | None,
        requested_version: str,
        **__,
    ) -> str:
        if not group_id or not artifact_id:
            raise PomPatchError("group_id and artifact_id required for update_dependency_version")

        dep_block = self._find_dependency_block(pom_content, group_id, artifact_id)
        if dep_block is None:
            raise PomPatchError(f"Dependency {group_id}:{artifact_id} not found in POM")

        block_text = pom_content[dep_block[0]:dep_block[1]]

        # Find <version> within the dependency block
        version_match = _re.search(r"<version>([^<]*)</version>", block_text)
        if not version_match:
            raise PomPatchError(f"No <version> element in dependency {group_id}:{artifact_id}")

        abs_start = dep_block[0] + version_match.start(1)
        abs_end = dep_block[0] + version_match.end(1)
        return pom_content[:abs_start] + requested_version + pom_content[abs_end:]

    # ── Remove dependency version ───────────────────────────────────

    def _patch_remove_dependency_version(
        self,
        pom_content: str,
        group_id: str | None,
        artifact_id: str | None,
        **__,
    ) -> str:
        if not group_id or not artifact_id:
            raise PomPatchError("group_id and artifact_id required for remove_dependency_version")

        dep_block = self._find_dependency_block(pom_content, group_id, artifact_id)
        if dep_block is None:
            raise PomPatchError(f"Dependency {group_id}:{artifact_id} not found in POM")

        block_text = pom_content[dep_block[0]:dep_block[1]]

        # Find <version>...</version> and remove it
        version_match = _re.search(r"(\s*)<version>[^<]*</version>(\s*\n?)", block_text)
        if not version_match:
            raise PomPatchError(f"No <version> in dependency {group_id}:{artifact_id}")

        abs_start = dep_block[0] + version_match.start()
        abs_end = dep_block[0] + version_match.end()
        return pom_content[:abs_start] + pom_content[abs_end:]

    # ── Plugin version patch ────────────────────────────────────────

    def _patch_plugin_version(
        self,
        pom_content: str,
        group_id: str | None,
        artifact_id: str | None,
        plugin_group_id: str | None,
        plugin_artifact_id: str | None,
        requested_version: str,
        **__,
    ) -> str:
        pg = plugin_group_id or group_id
        pa = plugin_artifact_id or artifact_id
        if not pg or not pa:
            raise PomPatchError("plugin_group_id and plugin_artifact_id required for update_plugin_version")

        plugin_block = self._find_plugin_block(pom_content, pg, pa)
        if plugin_block is None:
            raise PomPatchError(f"Plugin {pg}:{pa} not found in POM")

        block_text = pom_content[plugin_block[0]:plugin_block[1]]
        version_match = _re.search(r"<version>([^<]*)</version>", block_text)
        if not version_match:
            raise PomPatchError(f"No <version> in plugin {pg}:{pa}")

        abs_start = plugin_block[0] + version_match.start(1)
        abs_end = plugin_block[0] + version_match.end(1)
        return pom_content[:abs_start] + requested_version + pom_content[abs_end:]

    # ── Block finders ───────────────────────────────────────────────

    def _find_dependency_block(
        self,
        pom_content: str,
        group_id: str,
        artifact_id: str,
    ) -> tuple[int, int] | None:
        """Find the [start, end) of a <dependency> block matching groupId:artifactId."""
        # Find dependency blocks
        dep_pattern = _re.compile(
            r"<dependency>\s*"
            r"<groupId>" + _re.escape(group_id) + r"</groupId>\s*"
            r"<artifactId>" + _re.escape(artifact_id) + r"</artifactId>",
            _re.DOTALL
        )
        match = dep_pattern.search(pom_content)
        if not match:
            return None

        # Find enclosing <dependency>...</dependency>
        dep_start = pom_content.rfind("<dependency>", 0, match.start() + len("<dependency>"))
        if dep_start == -1:
            # Fallback: the match itself starts with <dependency>
            dep_start = match.start()
        if dep_start == -1:
            return None
        dep_end_raw = pom_content.find("</dependency>", match.end())
        if dep_end_raw == -1:
            return None
        dep_end = dep_end_raw + len("</dependency>")
        return (dep_start, dep_end)

    def _find_plugin_block(
        self,
        pom_content: str,
        group_id: str,
        artifact_id: str,
    ) -> tuple[int, int] | None:
        """Find the [start, end) of a <plugin> block."""
        plugin_pattern = _re.compile(
            r"<plugin>\s*"
            r"<groupId>" + _re.escape(group_id) + r"</groupId>\s*"
            r"<artifactId>" + _re.escape(artifact_id) + r"</artifactId>",
            _re.DOTALL
        )
        match = plugin_pattern.search(pom_content)
        if not match:
            return None

        plugin_start = pom_content.rfind("<plugin>", 0, match.start() + len("<plugin>"))
        if plugin_start == -1:
            plugin_start = match.start()
        plugin_end_raw = pom_content.find("</plugin>", match.end())
        if plugin_end_raw == -1:
            return None
        plugin_end = plugin_end_raw + len("</plugin>")
        return (plugin_start, plugin_end)

    # ── Version extraction ──────────────────────────────────────────

    def _extract_current_version(
        self,
        pom_content: str,
        target_kind: str,
        group_id: str | None,
        artifact_id: str | None,
        property_name: str | None,
        plugin_group_id: str | None,
        plugin_artifact_id: str | None,
    ) -> str:
        if target_kind == "property" and property_name:
            pattern = _re.compile(rf"<{_re.escape(property_name)}>([^<]*)</{_re.escape(property_name)}>")
            m = pattern.search(pom_content)
            return m.group(1) if m else ""

        if target_kind == "dependency" and group_id and artifact_id:
            block = self._find_dependency_block(pom_content, group_id, artifact_id)
            if block:
                block_text = pom_content[block[0]:block[1]]
                m = _re.search(r"<version>([^<]*)</version>", block_text)
                return m.group(1) if m else ""
            return ""

        if target_kind == "plugin":
            pg = plugin_group_id or group_id
            pa = plugin_artifact_id or artifact_id
            if pg and pa:
                block = self._find_plugin_block(pom_content, pg, pa)
                if block:
                    block_text = pom_content[block[0]:block[1]]
                    m = _re.search(r"<version>([^<]*)</version>", block_text)
                    return m.group(1) if m else ""

        return ""


# ── Helpers ────────────────────────────────────────────────────────

class PomPatchError(Exception):
    """Error during POM patching."""


def _sha256(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]


def _unified_diff(before: str, after: str) -> str:
    diff_lines = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="pom.xml (before)",
            tofile="pom.xml (after)",
        )
    )
    return "".join(diff_lines)


def _is_well_formed_xml(content: str) -> bool:
    try:
        ET.fromstring(content)
        return True
    except ET.ParseError:
        return False


def _target_desc(
    target_kind: str,
    group_id: str | None,
    artifact_id: str | None,
    property_name: str | None,
) -> str:
    if target_kind == "property":
        return f"property:{property_name}"
    if group_id and artifact_id:
        return f"{group_id}:{artifact_id}"
    if property_name:
        return f"property:{property_name}"
    return target_kind
