"""Focused tests for V1-11B: Bounded evidence retrievers."""

from __future__ import annotations

import json

import pytest

from migration_factory.control_tower.application.retrievers import (
    BoundedEvidenceRetriever,
    EvidenceBounds,
    EvidenceBoundsError,
    EvidenceRef,
    build_evidence_refs_json,
)


# ── Test double ────────────────────────────────────────────────────


class FakeEvidenceSource:
    """In-memory evidence source for testing bounded retrieval."""

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}
        self._entries: dict[str, list[dict[str, object]]] = {}

    def add_file(self, source_type: str, source_id: str, relative_path: str, content: bytes) -> None:
        key = f"{source_type}:{source_id}:{relative_path}"
        self._files[key] = content

    def add_entry(self, source_type: str, source_id: str, relative_path: str, size_bytes: int = 0) -> None:
        key = f"{source_type}:{source_id}"
        if key not in self._entries:
            self._entries[key] = []
        self._entries[key].append({
            "relative_path": relative_path,
            "size_bytes": size_bytes,
            "content_type": None,
        })

    def read_bytes(
        self,
        *,
        source_type: str,
        source_id: str,
        relative_path: str,
        max_bytes: int,
    ) -> bytes | None:
        key = f"{source_type}:{source_id}:{relative_path}"
        raw = self._files.get(key)
        if raw is None:
            return None
        return raw[:max_bytes]

    def resolve_entries(
        self,
        *,
        source_type: str,
        source_id: str,
        prefix: str,
        max_files: int,
        max_depth: int,
    ) -> tuple[dict[str, object], ...]:
        key = f"{source_type}:{source_id}"
        all_entries = self._entries.get(key, [])
        filtered = [e for e in all_entries if str(e.get("relative_path", "")).startswith(prefix)]
        return tuple(filtered[:max_files])


@pytest.fixture
def fake_source() -> FakeEvidenceSource:
    return FakeEvidenceSource()


@pytest.fixture
def retriever(fake_source: FakeEvidenceSource) -> BoundedEvidenceRetriever:
    return BoundedEvidenceRetriever(evidence_source=fake_source)


# ── EvidenceBounds tests ───────────────────────────────────────────


class TestEvidenceBounds:
    """EvidenceBounds dataclass defaults and construction."""

    def test_default_bounds(self) -> None:
        bounds = EvidenceBounds()
        assert bounds.max_bytes == 1_000_000
        assert bounds.max_files == 50
        assert bounds.max_depth == 5
        assert bounds.allow_absolute_paths is False

    def test_custom_bounds(self) -> None:
        bounds = EvidenceBounds(
            max_bytes=5000,
            max_files=10,
            max_depth=2,
            allow_absolute_paths=True,
            allowed_prefixes=("src/",),
            forbidden_suffixes=(".exe",),
        )
        assert bounds.max_bytes == 5000
        assert bounds.max_files == 10
        assert bounds.max_depth == 2
        assert bounds.allow_absolute_paths is True
        assert bounds.forbidden_suffixes == (".exe",)

    def test_is_frozen(self) -> None:
        bounds = EvidenceBounds()
        with pytest.raises(AttributeError):
            bounds.max_bytes = 999  # type: ignore[misc]

    def test_is_slotted(self) -> None:
        bounds = EvidenceBounds()
        assert hasattr(bounds, "__slots__")


# ── BoundedEvidenceRetriever tests ─────────────────────────────────


class TestBoundedEvidenceRetriever:
    """BoundedEvidenceRetriever retrieval behavior."""

    def test_retrieve_single_file(self, retriever: BoundedEvidenceRetriever, fake_source: FakeEvidenceSource) -> None:
        content = b"package com.example; public class App {}"
        fake_source.add_file("artifact", "art-001", "src/main/java/App.java", content)
        fake_source.add_entry("artifact", "art-001", "src/main/java/App.java", len(content))

        refs = retriever.retrieve_evidence(
            source_type="artifact",
            source_id="art-001",
            evidence_paths=("src/main/java/App.java",),
        )

        assert len(refs) == 1
        assert refs[0].source_type == "artifact"
        assert refs[0].source_id == "art-001"
        assert refs[0].relative_path == "src/main/java/App.java"
        assert refs[0].size_bytes == len(content)

    def test_empty_paths_returns_empty(self, retriever: BoundedEvidenceRetriever) -> None:
        refs = retriever.retrieve_evidence(
            source_type="artifact",
            source_id="art-001",
            evidence_paths=(),
        )
        assert refs == ()

    def test_rejects_absolute_path(self, retriever: BoundedEvidenceRetriever) -> None:
        with pytest.raises(EvidenceBoundsError, match="Absolute path"):
            retriever.retrieve_evidence(
                source_type="artifact",
                source_id="art-001",
                evidence_paths=("/etc/passwd",),
            )

    def test_rejects_deep_path(self, retriever: BoundedEvidenceRetriever) -> None:
        bounds = EvidenceBounds(max_depth=2)
        with pytest.raises(EvidenceBoundsError, match="depth"):
            retriever.retrieve_evidence(
                source_type="artifact",
                source_id="art-001",
                evidence_paths=("a/b/c/d/file.java",),
                bounds=bounds,
            )

    def test_rejects_forbidden_suffix(self, retriever: BoundedEvidenceRetriever) -> None:
        with pytest.raises(EvidenceBoundsError, match="forbidden suffix"):
            retriever.retrieve_evidence(
                source_type="artifact",
                source_id="art-001",
                evidence_paths=("build/output.jar",),
            )

    def test_rejects_paths_exceeding_max_files(self, retriever: BoundedEvidenceRetriever) -> None:
        bounds = EvidenceBounds(max_files=2)
        with pytest.raises(EvidenceBoundsError, match="exceeds max_files"):
            retriever.retrieve_evidence(
                source_type="artifact",
                source_id="art-001",
                evidence_paths=("a.java", "b.java", "c.java"),
                bounds=bounds,
            )

    def test_allowed_prefix_filter(self, retriever: BoundedEvidenceRetriever) -> None:
        bounds = EvidenceBounds(max_depth=5, allowed_prefixes=("src/",))
        with pytest.raises(EvidenceBoundsError, match="allowed prefix"):
            retriever.retrieve_evidence(
                source_type="artifact",
                source_id="art-001",
                evidence_paths=("target/classes/Main.class",),
                bounds=bounds,
            )

    def test_content_type_and_checksum(self, retriever: BoundedEvidenceRetriever, fake_source: FakeEvidenceSource) -> None:
        content = b"public class Hello {}"
        fake_source.add_file("artifact", "art-002", "Hello.java", content)
        fake_source.add_entry("artifact", "art-002", "Hello.java", len(content))

        refs = retriever.retrieve_evidence(
            source_type="artifact",
            source_id="art-002",
            evidence_paths=("Hello.java",),
        )
        assert len(refs) == 1
        ref = refs[0]
        assert ref.content_type == "application/octet-stream"
        assert ref.checksum_algorithm == "sha256"
        assert isinstance(ref.checksum, str)
        assert len(ref.checksum) == 64  # sha256 hex

    def test_resolve_and_retrieve(self, retriever: BoundedEvidenceRetriever, fake_source: FakeEvidenceSource) -> None:
        content_a = b"class A {}"
        content_b = b"class B {}"
        fake_source.add_file("artifact", "art-003", "src/A.java", content_a)
        fake_source.add_file("artifact", "art-003", "src/B.java", content_b)
        fake_source.add_entry("artifact", "art-003", "src/A.java", len(content_a))
        fake_source.add_entry("artifact", "art-003", "src/B.java", len(content_b))

        refs = retriever.resolve_and_retrieve(
            source_type="artifact",
            source_id="art-003",
            prefix="src/",
        )

        assert len(refs) == 2
        paths = {ref.relative_path for ref in refs}
        assert paths == {"src/A.java", "src/B.java"}

    def test_resolve_and_retrieve_no_entries(self, retriever: BoundedEvidenceRetriever) -> None:
        refs = retriever.resolve_and_retrieve(
            source_type="artifact",
            source_id="art-missing",
            prefix="src/",
        )
        assert refs == ()


# ── build_evidence_refs_json tests ─────────────────────────────────


class TestBuildEvidenceRefsJson:
    """build_evidence_refs_json produces deterministic JSON."""

    def test_empty_refs(self) -> None:
        result = build_evidence_refs_json(())
        assert result == "[]"

    def test_single_ref(self) -> None:
        ref = EvidenceRef(
            source_type="artifact",
            source_id="art-001",
            relative_path="src/Main.java",
            content_type="text/x-java",
            size_bytes=100,
            checksum_algorithm="sha256",
            checksum="abc123" + "0" * 58,
            metadata_json='{"source_type":"artifact"}',
        )
        result = build_evidence_refs_json((ref,))
        parsed = json.loads(result)
        assert len(parsed) == 1
        assert parsed[0]["source_type"] == "artifact"
        assert parsed[0]["relative_path"] == "src/Main.java"

    def test_deterministic_order(self) -> None:
        ref_a = EvidenceRef(
            source_type="artifact",
            source_id="art-a",
            relative_path="a.java",
            size_bytes=10,
            checksum="1",
        )
        ref_b = EvidenceRef(
            source_type="artifact",
            source_id="art-b",
            relative_path="b.java",
            size_bytes=20,
            checksum="2",
        )
        result1 = build_evidence_refs_json((ref_a, ref_b))
        result2 = build_evidence_refs_json((ref_b, ref_a))
        # sorted by keys should produce the same output regardless of input order
        assert result1 == result2


# ── Edge case tests ────────────────────────────────────────────────


class TestRetrievalEdgeCases:
    """Edge case tests for bounded evidence retrieval."""

    def test_missing_source_returns_none_safely(self, retriever: BoundedEvidenceRetriever) -> None:
        refs = retriever.retrieve_evidence(
            source_type="nonexistent",
            source_id="no-such-id",
            evidence_paths=("test.java",),
        )
        # The source doesn't exist, so read_bytes returns None -> ref is skipped
        assert refs == ()

    def test_exceeds_total_max_bytes(self, retriever: BoundedEvidenceRetriever, fake_source: FakeEvidenceSource) -> None:
        bounds = EvidenceBounds(max_bytes=50, max_depth=5)
        content_a = b"x" * 30
        content_b = b"y" * 30
        fake_source.add_file("artifact", "art-byte", "a.txt", content_a)
        fake_source.add_file("artifact", "art-byte", "b.txt", content_b)
        fake_source.add_entry("artifact", "art-byte", "a.txt", len(content_a))
        fake_source.add_entry("artifact", "art-byte", "b.txt", len(content_b))

        with pytest.raises(EvidenceBoundsError, match="exceeds max_bytes"):
            retriever.retrieve_evidence(
                source_type="artifact",
                source_id="art-byte",
                evidence_paths=("a.txt", "b.txt"),
                bounds=bounds,
            )

    def test_path_traversal_prevention(self, retriever: BoundedEvidenceRetriever) -> None:
        with pytest.raises(EvidenceBoundsError, match="Path traversal"):
            retriever.retrieve_evidence(
                source_type="artifact",
                source_id="art-001",
                evidence_paths=("../../etc/passwd",),
            )

    def test_absolute_path_still_rejected(self, retriever: BoundedEvidenceRetriever) -> None:
        with pytest.raises(EvidenceBoundsError, match="Absolute path"):
            retriever.retrieve_evidence(
                source_type="artifact",
                source_id="art-001",
                evidence_paths=("/etc/passwd",),
            )

    def test_absolute_path_windows_rooted_rejected(self, retriever: BoundedEvidenceRetriever) -> None:
        """Windows-rooted path like \\foo\bar is rejected."""
        with pytest.raises(EvidenceBoundsError, match="Absolute path"):
            retriever.retrieve_evidence(
                source_type="artifact",
                source_id="art-001",
                evidence_paths=("\\windows\\system32\\config",),
            )

    def test_absolute_path_windows_drive_backslash_rejected(self, retriever: BoundedEvidenceRetriever) -> None:
        """Windows absolute path C:\foo\bar is rejected."""
        with pytest.raises(EvidenceBoundsError, match="Absolute path"):
            retriever.retrieve_evidence(
                source_type="artifact",
                source_id="art-001",
                evidence_paths=("C:\\Users\\evil\\secrets.txt",),
            )

    def test_absolute_path_windows_drive_forwardslash_rejected(self, retriever: BoundedEvidenceRetriever) -> None:
        """Windows absolute path C:/foo/bar is rejected."""
        with pytest.raises(EvidenceBoundsError, match="Absolute path"):
            retriever.retrieve_evidence(
                source_type="artifact",
                source_id="art-001",
                evidence_paths=("C:/Users/evil/secrets.txt",),
            )

    def test_absolute_path_unc_backslash_rejected(self, retriever: BoundedEvidenceRetriever) -> None:
        """UNC path \\\\server\\share is rejected."""
        with pytest.raises(EvidenceBoundsError, match="Absolute path"):
            retriever.retrieve_evidence(
                source_type="artifact",
                source_id="art-001",
                evidence_paths=("\\\\server\\share\\malicious.exe",),
            )

    def test_absolute_path_unc_forwardslash_rejected(self, retriever: BoundedEvidenceRetriever) -> None:
        """UNC path //server/share is rejected."""
        with pytest.raises(EvidenceBoundsError, match="Absolute path"):
            retriever.retrieve_evidence(
                source_type="artifact",
                source_id="art-001",
                evidence_paths=("//server/share/malicious.exe",),
            )

    def test_absolute_path_file_uri_rejected(self, retriever: BoundedEvidenceRetriever) -> None:
        """file:// URI scheme is rejected."""
        with pytest.raises(EvidenceBoundsError, match="Absolute path"):
            retriever.retrieve_evidence(
                source_type="artifact",
                source_id="art-001",
                evidence_paths=("file:///etc/passwd",),
            )

    def test_safe_relative_path_accepted(self, retriever: BoundedEvidenceRetriever) -> None:
        """Plain relative paths still pass validation."""
        refs = retriever.retrieve_evidence(
            source_type="artifact",
            source_id="art-001",
            evidence_paths=("src/main/App.java",),
        )
        assert refs == ()  # no exception = accepted (source missing = empty)

    def test_empty_path_skipped(self, retriever: BoundedEvidenceRetriever) -> None:
        refs = retriever.retrieve_evidence(
            source_type="artifact",
            source_id="art-001",
            evidence_paths=("", "  ", "valid.java"),
        )
        # Only valid.java should be processed (but source doesn't exist)
        assert refs == ()
