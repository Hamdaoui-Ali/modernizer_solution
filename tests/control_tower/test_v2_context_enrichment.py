"""Tests for V2 context pack enrichment (F01).

Tests that enrichment metadata is correctly:
1. Attached to context packs during build
2. Redacted before storage/prompt construction
3. Extracted from persisted manifests
4. Backward compatible with old packs without metadata
"""

from __future__ import annotations

import json

import pytest

from migration_factory.control_tower.application.v2_model_schemas import (
    ContextPackBuilder,
    ContextPack,
)
from migration_factory.control_tower.application.context_pack_redaction import (
    redact_context_pack_metadata,
    build_metadata_dict,
)
from migration_factory.control_tower.application.dto import (
    ContextPackManifestDto,
)


# ── ContextPack enrichment tests ────────────────────────────────────


class TestContextPackEnrichmentMetadata:

    def test_build_with_full_metadata(self) -> None:
        """Build a context pack with all enrichment metadata."""
        pack = ContextPackBuilder.build_context_pack(
            pack_type="repair_proposal",
            title="Fixing compilation error",
            description="Stage 1 build failed",
            evidence_refs=("/tmp/build.log", "/tmp/pom.xml"),
            agent_name="failure_diagnosis",
            event_type="build_failed",
            stage_index=1,
            profile_id="azure-proposer",
            command_id="cmd-build-001",
            failure_type="compilation_error",
            artifact_refs_used=("build.log", "pom.xml", "target/classes/Main.class"),
            pom_summary_ref="pom-summary-001",
            sandbox_binding_ref="sandbox-stage-1",
            redaction_status="redacted_2_paths",
        )
        assert pack.agent_name == "failure_diagnosis"
        assert pack.event_type == "build_failed"
        assert pack.stage_index == 1
        assert pack.artifact_refs_used == ("build.log", "pom.xml", "target/classes/Main.class")
        assert pack.sandbox_binding_ref == "sandbox-stage-1"

    def test_build_with_partial_metadata(self) -> None:
        """Partial metadata does not affect defaults."""
        pack = ContextPackBuilder.build_context_pack(
            pack_type="plan_proposal",
            title="Stage plan",
            description="Plan description",
            evidence_refs=(),
            agent_name="planner",
            event_type="plan_requested",
        )
        assert pack.agent_name == "planner"
        assert pack.event_type == "plan_requested"
        # Unset fields remain None
        assert pack.stage_index is None
        assert pack.failure_type is None
        assert pack.pom_summary_ref is None

    def test_metadata_redacted_before_storage(self) -> None:
        """Metadata is redacted via redact_context_pack_metadata."""
        meta: dict[str, object] = {
            "agent_name": "diagnosis",
            "command_id": "cmd-001",
            "sandbox_binding_ref": "/home/user/sandbox/stage-1",
            "artifact_refs_used": ["/home/user/project/build.log", "pom.xml"],
            "profile_id": "azure-proposer",
        }
        redacted = redact_context_pack_metadata(meta)
        # Non-sensitive fields pass through
        assert redacted["agent_name"] == "diagnosis"
        assert redacted["command_id"] == "cmd-001"
        # Paths are redacted
        assert "[redacted" in str(redacted["sandbox_binding_ref"])
        refs = redacted["artifact_refs_used"]
        assert isinstance(refs, list)
        assert any("[redacted" in str(r) for r in refs)

    def test_build_metadata_dict(self) -> None:
        """build_metadata_dict extracts only non-empty metadata."""
        pack = ContextPackBuilder.build_context_pack(
            pack_type="repair_proposal",
            title="Test metadata dict",
            description="desc",
            evidence_refs=(),
            agent_name="diag",
            event_type="build_failed",
            stage_index=2,
            failure_type="test_error",
        )
        meta = build_metadata_dict(pack)
        assert meta == {
            "agent_name": "diag",
            "event_type": "build_failed",
            "stage_index": 2,
            "failure_type": "test_error",
        }

    def test_build_metadata_dict_empty(self) -> None:
        """build_metadata_dict returns empty dict when no metadata set."""
        pack = ContextPackBuilder.build_context_pack(
            pack_type="assistant_answer",
            title="No meta",
            description="desc",
            evidence_refs=(),
        )
        meta = build_metadata_dict(pack)
        assert meta == {}


# ── Manifest enrichment tests ──────────────────────────────────────


class TestManifestEnrichment:

    def test_persist_manifest_with_enrichment(self) -> None:
        """Enrichment metadata is properly embedded in bounds_json."""
        # Simulate what ContextPackManifestService does internally
        enrichment: dict[str, object] = {
            "agent_name": "diagnosis",
            "event_type": "build_failed",
            "stage_index": 1,
            "profile_id": "azure-proposer",
            "command_id": "cmd-001",
            "failure_type": "compilation_error",
        }
        redacted = redact_context_pack_metadata(enrichment)
        existing_bounds: dict[str, object] = {}
        existing_bounds["_enrichment"] = redacted
        bounds_json = json.dumps(existing_bounds, separators=(",", ":"), sort_keys=True)

        parsed = json.loads(bounds_json)
        assert "_enrichment" in parsed
        meta = parsed["_enrichment"]
        assert meta["agent_name"] == "diagnosis"
        assert meta["event_type"] == "build_failed"
        assert meta["stage_index"] == 1

    def test_extract_enrichment_from_bounds(self) -> None:
        """Enrichment metadata can be extracted from bounds_json."""
        bounds = json.dumps({
            "max_files": 100,
            "_enrichment": {
                "agent_name": "diag",
                "event_type": "build_failed",
            },
        })
        parsed = json.loads(bounds)
        enrichment = parsed.get("_enrichment", {})
        assert enrichment["agent_name"] == "diag"
        assert enrichment["event_type"] == "build_failed"

    def test_extract_enrichment_missing_bounds(self) -> None:
        """Missing bounds_json returns empty enrichment."""
        enrichment = {}
        assert enrichment == {}

    def test_extract_enrichment_empty_bounds(self) -> None:
        """bounds_json without _enrichment key returns empty."""
        bounds = json.dumps({"max_files": 50})
        parsed = json.loads(bounds)
        enrichment = parsed.get("_enrichment", {})
        assert enrichment == {}

    def test_manifest_dto_includes_enrichment(self) -> None:
        """ContextPackManifestDto carries optional enrichment_metadata."""
        dto = ContextPackManifestDto(
            manifest_id="cp-test",
            pack_type="repair_proposal",
            pack_version="1.0",
            title="Test",
            enrichment_metadata={
                "agent_name": "diagnosis",
                "event_type": "build_failed",
            },
        )
        assert dto.enrichment_metadata is not None
        assert dto.enrichment_metadata["agent_name"] == "diagnosis"

    def test_manifest_dto_backward_compatible(self) -> None:
        """Old DTOs without enrichment_metadata remain valid."""
        dto = ContextPackManifestDto(
            manifest_id="cp-old",
            pack_type="plan_proposal",
            pack_version="1.0",
            title="Old pack",
        )
        assert dto.enrichment_metadata is None


# ── Integration-style: builder + redaction + dict ──────────────────


def test_full_enrichment_pipeline() -> None:
    """End-to-end: build, metadata dict, redact, to_dict."""
    pack = ContextPackBuilder.build_context_pack(
        pack_type="repair_proposal",
        title="Full pipeline",
        description="Stage 1 failure",
        evidence_refs=("/tmp/build.log",),
        agent_name="failure_diagnosis",
        event_type="build_failed",
        stage_index=1,
        profile_id="azure-proposer",
        command_id="cmd-build-001",
    )
    # Extract metadata
    meta = build_metadata_dict(pack)
    assert meta["agent_name"] == "failure_diagnosis"

    # Redact
    redacted = redact_context_pack_metadata(meta)
    assert redacted["agent_name"] == "failure_diagnosis"

    # to_dict includes metadata
    d = ContextPackBuilder.pack_to_dict(pack)
    assert d["agent_name"] == "failure_diagnosis"
    assert d["event_type"] == "build_failed"
    assert d["stage_index"] == 1
    assert d["command_id"] == "cmd-build-001"
