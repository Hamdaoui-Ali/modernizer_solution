"""Focused tests for F15-JOB-051 — Gate artifact ref schema."""

import json
import pytest

from migration_factory.control_tower.domain.gate_artifact_ref import (
    ArtifactKind,
    GateArtifactRef,
    parse_artifact_refs,
    serialize_artifact_refs,
    validate_artifact_ref,
    validate_all_artifact_refs,
    ArtifactRefValidationError,
    artifact_ref_to_public_dto,
    build_artifact_refs,
)


class TestArtifactRefSchema:
    """GateArtifactRef schema construction, serialization, and validation."""

    def test_build_single_ref(self):
        """Build a single analysis_report ref."""
        refs = build_artifact_refs([(
            "analysis_report",
            "artifacts/analysis/summary.json",
            "abc123def456",
        )])
        assert len(refs) == 1
        assert refs[0].kind == "analysis_report"
        assert refs[0].path_or_ref == "artifacts/analysis/summary.json"
        assert refs[0].checksum == "abc123def456"
        assert refs[0].description == ""

    def test_build_multiple_refs(self):
        """Build multiple refs of different kinds."""
        refs = build_artifact_refs([
            ("analysis_report", "artifacts/analysis/summary.json", "chk1", "Analysis findings"),
            ("dependency_graph", "artifacts/deps/graph.dot", "chk2", "Dependency visualization"),
            ("test_inventory", "artifacts/tests/inventory.csv", "chk3", "Test suite map"),
        ])
        assert len(refs) == 3
        assert refs[0].kind == "analysis_report"
        assert refs[1].kind == "dependency_graph"
        assert refs[2].kind == "test_inventory"

    def test_parse_from_json_string(self):
        """Parse artifact refs from a JSON string (as stored in gate records)."""
        raw = json.dumps([
            {"kind": "analysis_report", "path_or_ref": "analysis/summary.json", "checksum": "chk1"},
            {"kind": "dependency_graph", "path_or_ref": "deps/graph.dot", "checksum": "chk2"},
        ])
        refs = parse_artifact_refs(raw)
        assert len(refs) == 2
        assert refs[0].kind == "analysis_report"
        assert refs[1].kind == "dependency_graph"

    def test_parse_from_json_bytes(self):
        """Parse artifact refs from bytes."""
        raw = json.dumps([
            {"kind": "analysis_report", "path_or_ref": "artifacts/analysis.json", "checksum": "chk1"},
        ]).encode("utf-8")
        refs = parse_artifact_refs(raw)
        assert len(refs) == 1

    def test_parse_from_list(self):
        """Parse artifact refs from a pre-parsed Python list."""
        raw = [
            {"kind": "analysis_report", "path_or_ref": "analysis.json", "checksum": "chk1"},
        ]
        refs = parse_artifact_refs(raw)
        assert len(refs) == 1

    def test_parse_single_dict(self):
        """Parse a single dict (not wrapped in list)."""
        raw = {"kind": "analysis_report", "path_or_ref": "analysis.json", "checksum": "chk1"}
        refs = parse_artifact_refs(raw)
        assert len(refs) == 1

    def test_parse_empty_string(self):
        """Empty or invalid input returns empty tuple."""
        assert parse_artifact_refs("") == ()
        assert parse_artifact_refs("[]") == ()
        assert parse_artifact_refs(None) == ()
        assert parse_artifact_refs("not json") == ()
        assert parse_artifact_refs(b"garbage") == ()

    def test_serialize_roundtrip(self):
        """Serialize and deserialize preserves refs."""
        original = build_artifact_refs([
            ("analysis_report", "analysis/summary.json", "chk1"),
            ("plan", "plan/migration.yaml", "chk2"),
        ])
        serialized = serialize_artifact_refs(original)
        parsed = parse_artifact_refs(serialized)
        assert len(parsed) == len(original)
        for p, o in zip(parsed, original):
            assert p.kind == o.kind
            assert p.checksum == o.checksum

    def test_serialize_redacts_absolute_paths(self):
        """Serialize redacts absolute filesystem paths."""
        refs = build_artifact_refs([
            ("analysis_report", "/home/user/sandbox/analysis.json", "chk1"),
            ("plan", "/tmp/migration/migration.yaml", "chk2"),
        ])
        serialized = serialize_artifact_refs(refs)
        assert "/home/user/" not in serialized
        assert "/tmp/migration/" not in serialized


class TestArtifactKind:
    """ArtifactKind enum behavior."""

    def test_known_kinds(self):
        """Known artifact kinds are recognized."""
        assert ArtifactKind.has_value("analysis_report")
        assert ArtifactKind.has_value("migration_plan")
        assert ArtifactKind.has_value("repair_proposal")

    def test_unknown_kind(self):
        """Unknown artifact kinds are not recognized."""
        assert not ArtifactKind.has_value("unknown_artifact_kind")

    def test_kind_values(self):
        """All kind enum values are strings."""
        for kind in ArtifactKind:
            assert isinstance(kind.value, str)
            assert kind.value


class TestValidation:
    """Artifact ref validation."""

    def test_valid_ref_passes(self):
        """A properly formed ref passes validation."""
        ref = GateArtifactRef(kind="analysis_report", path_or_ref="artifacts/analysis.json", checksum="a1b2c3d4e5f6")
        validate_artifact_ref(ref)  # should not raise

    def test_missing_kind_raises(self):
        """Missing kind is rejected."""
        ref = GateArtifactRef(kind="", path_or_ref="analysis.json", checksum="a1b2c3d4")
        with pytest.raises(ArtifactRefValidationError, match="kind"):
            validate_artifact_ref(ref)

    def test_missing_path_raises(self):
        """Missing path_or_ref is rejected."""
        ref = GateArtifactRef(kind="analysis_report", path_or_ref="", checksum="a1b2c3d4")
        with pytest.raises(ArtifactRefValidationError, match="path_or_ref"):
            validate_artifact_ref(ref)

    def test_missing_checksum_raises(self):
        """Missing checksum is rejected."""
        ref = GateArtifactRef(kind="analysis_report", path_or_ref="analysis.json", checksum="")
        with pytest.raises(ArtifactRefValidationError, match="checksum"):
            validate_artifact_ref(ref)

    def test_short_checksum_raises(self):
        """Short checksum (less than 8 chars) is rejected."""
        ref = GateArtifactRef(kind="analysis_report", path_or_ref="analysis.json", checksum="short")
        with pytest.raises(ArtifactRefValidationError, match="checksum"):
            validate_artifact_ref(ref)

    def test_validate_all(self):
        """validate_all_artifact_refs validates all refs."""
        refs = build_artifact_refs([
            ("analysis_report", "analysis.json", "abcdef123456"),
            ("", "plan.yaml", "chk2"),  # invalid - empty kind
        ])
        with pytest.raises(ArtifactRefValidationError):
            validate_all_artifact_refs(refs)


class TestDTO:
    """Public DTO conversion."""

    def test_dto_redacts_absolute_path(self):
        """Public DTO redacts absolute filesystem paths."""
        ref = GateArtifactRef(
            kind="analysis_report",
            path_or_ref="/home/user/sandbox/secret-analysis.json",
            checksum="abc123def456",
        )
        dto = artifact_ref_to_public_dto(ref)
        assert "/home/user/" not in dto["path_or_ref"]
        assert dto["checksum"] == "abc123def456"
        assert dto["kind"] == "analysis_report"

    def test_dto_preserves_relative_path(self):
        """Public DTO preserves relative paths unchanged."""
        ref = GateArtifactRef(
            kind="analysis_report",
            path_or_ref="artifacts/analysis/summary.json",
            checksum="abc123def456",
            description="Analysis findings",
        )
        dto = artifact_ref_to_public_dto(ref)
        assert dto["path_or_ref"] == "artifacts/analysis/summary.json"
        assert dto["description"] == "Analysis findings"

    def test_dtos_list(self):
        """Multiple DTOs can be generated."""
        from migration_factory.control_tower.domain.gate_artifact_ref import artifact_refs_to_public_dtos
        refs = build_artifact_refs([
            ("analysis_report", "analysis.json", "chk1"),
            ("plan", "plan.yaml", "chk2"),
        ])
        dtos = artifact_refs_to_public_dtos(refs)
        assert len(dtos) == 2
        for dto in dtos:
            assert "kind" in dto
            assert "path_or_ref" in dto
            assert "checksum" in dto
