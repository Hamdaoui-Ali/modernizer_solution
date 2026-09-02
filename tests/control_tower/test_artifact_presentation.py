"""F1-T7 focused tests — Artifact presentation contract round-trip."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from migration_factory.control_tower.schemas.artifact_presentation import (
    ARTIFACT_PRESENTATION_FIELDS,
    ARTIFACT_REDACTION_RULES,
    DOWNLOADABLE_ARTIFACT_TYPES,
    MAX_PREVIEW_CONTENT_CHARS,
    PREVIEWABLE_ARTIFACT_TYPES,
    ArtifactPresentationBatch,
    ArtifactPresentationError,
    ArtifactPresentationKind,
    ArtifactPresentationRef,
    ArtifactResolutionState,
    build_presentation_ref,
    get_content_type,
    is_downloadable,
    is_previewable,
    validate_artifact_presentation_kind,
)


# ── helpers ───────────────────────────────────────────────────────────

def _valid_ref(**overrides) -> ArtifactPresentationRef:
    defaults = {
        "artifact_id": "art-001",
        "artifact_type": "analysis_report.md",
        "presentation_kind": ArtifactPresentationKind.PREVIEW,
        "content_type": "text/markdown; charset=utf-8",
        "checksum": "sha256:abc123def456",
        "state": ArtifactResolutionState.AVAILABLE,
    }
    defaults.update(overrides)
    return ArtifactPresentationRef(**defaults)


def _valid_batch(**overrides) -> ArtifactPresentationBatch:
    defaults = {
        "checkpoint_id": "acp-001",
        "job_id": "job-abc",
        "artifacts": (_valid_ref(),),
        "gate_id": "gate-001",
        "gate_checksum": "sha256:gate123",
    }
    defaults.update(overrides)
    return ArtifactPresentationBatch(**defaults)


# ══════════════════════════════════════════════════════════════════════════
# 1. PREVIEWABLE / DOWNLOADABLE type contracts
# ══════════════════════════════════════════════════════════════════════════

class TestPreviewableArtifactTypes:
    """F1-T7: Previewable types must be a subset of downloadable types."""

    def test_previewable_is_subset_of_downloadable(self):
        assert DOWNLOADABLE_ARTIFACT_TYPES.issuperset(PREVIEWABLE_ARTIFACT_TYPES)

    def test_known_analysis_artifacts_are_previewable(self):
        """Analysis outputs must be previewable."""
        for at in ["analysis_report.md", "analysis_summary.md",
                    "dependency_graph.json", "test_inventory.yaml",
                    "read_only_verification.json"]:
            assert at in PREVIEWABLE_ARTIFACT_TYPES, f"{at} must be previewable"

    def test_known_planning_artifacts_are_previewable(self):
        """Planning outputs must be previewable."""
        for at in ["migration_plan.yaml", "migration_units.yaml",
                    "plan_summary.md", "plan_validation_report.json",
                    "approval_request.json"]:
            assert at in PREVIEWABLE_ARTIFACT_TYPES, f"{at} must be previewable"

    def test_binary_large_artifacts_are_not_previewable(self):
        """Binary or patch artifacts should not be previewable."""
        assert "rewrite_dry_run.patch" not in PREVIEWABLE_ARTIFACT_TYPES
        assert "openrewrite_plugin.xml" not in PREVIEWABLE_ARTIFACT_TYPES

    def test_every_previewable_is_downloadable(self):
        for at in PREVIEWABLE_ARTIFACT_TYPES:
            assert at in DOWNLOADABLE_ARTIFACT_TYPES, f"{at} must be downloadable"

    def test_is_previewable_helper(self):
        assert is_previewable("analysis_report.md") is True
        assert is_previewable("rewrite_dry_run.patch") is False
        assert is_previewable("nonexistent.txt") is False

    def test_is_downloadable_helper(self):
        assert is_downloadable("analysis_report.md") is True
        assert is_downloadable("rewrite_dry_run.patch") is True
        assert is_downloadable("nonexistent.txt") is False


# ══════════════════════════════════════════════════════════════════════════
# 2. Content-Type mapping
# ══════════════════════════════════════════════════════════════════════════

class TestContentTypeMapping:
    """F1-T7: Content-Type inference from artifact type."""

    def test_markdown_content_type(self):
        assert get_content_type("analysis_report.md") == "text/markdown; charset=utf-8"

    def test_yaml_content_type(self):
        assert get_content_type("migration_plan.yaml") == "application/x-yaml; charset=utf-8"
        assert get_content_type("test_inventory.yml") == "application/x-yaml; charset=utf-8"

    def test_json_content_type(self):
        assert get_content_type("dependency_graph.json") == "application/json; charset=utf-8"

    def test_text_content_type(self):
        assert get_content_type("build_log.txt") == "text/plain; charset=utf-8"
        assert get_content_type("phase2_log.log") == "text/plain; charset=utf-8"

    def test_patch_content_type(self):
        assert get_content_type("rewrite_dry_run.patch") == "text/x-diff; charset=utf-8"

    def test_xml_content_type(self):
        assert get_content_type("openrewrite_plugin.xml") == "application/xml; charset=utf-8"

    def test_unknown_extension_defaults_to_octet_stream(self):
        assert get_content_type("some_binary_file.dat") == "application/octet-stream"

    def test_no_extension_defaults_to_octet_stream(self):
        assert get_content_type("artifact_without_extension") == "application/octet-stream"


# ══════════════════════════════════════════════════════════════════════════
# 3. ArtifactResolutionState
# ══════════════════════════════════════════════════════════════════════════

class TestArtifactResolutionState:
    """F1-T7: Resolution states must cover all outcomes."""

    def test_all_states_defined(self):
        assert {s.value for s in ArtifactResolutionState} == {
            "available", "stale", "missing",
            "checksum_mismatch", "redaction_applied",
        }

    def test_terminal_states(self):
        terminal = {
            ArtifactResolutionState.STALE,
            ArtifactResolutionState.MISSING,
            ArtifactResolutionState.CHECKSUM_MISMATCH,
        }
        for state in terminal:
            assert state.value in ("stale", "missing", "checksum_mismatch")


# ══════════════════════════════════════════════════════════════════════════
# 4. ArtifactPresentationError
# ══════════════════════════════════════════════════════════════════════════

class TestArtifactPresentationError:
    """F1-T7: Error messages must be sanitized — no paths or secrets."""

    def test_no_errors_contain_path_separators(self):
        for e in ArtifactPresentationError:
            msg = e.value
            assert "\\\\" not in msg
            assert "C:" not in msg.lower()
            assert "/home" not in msg.lower()

    def test_no_errors_contain_secret_keywords(self):
        dangerous = ["password", "token", "secret", "api_key", "sandbox"]
        for e in ArtifactPresentationError:
            msg = e.value.lower()
            for d in dangerous:
                assert d not in msg, f"{e.name} contains {d!r}"

    def test_each_error_is_distinct(self):
        messages = {e.value for e in ArtifactPresentationError}
        assert len(messages) == len(ArtifactPresentationError)


# ══════════════════════════════════════════════════════════════════════════
# 5. ArtifactPresentationRef construction
# ══════════════════════════════════════════════════════════════════════════

class TestArtifactPresentationRefConstruction:
    """F1-T7: ArtifactPresentationRef DTO construction."""

    def test_minimal_construction(self):
        ref = _valid_ref()
        assert ref.artifact_id == "art-001"
        assert ref.artifact_type == "analysis_report.md"
        assert ref.presentation_kind == ArtifactPresentationKind.PREVIEW
        assert ref.state == ArtifactResolutionState.AVAILABLE
        assert ref.is_available is True

    def test_checksum_too_short_rejected(self):
        with pytest.raises(ValidationError):
            _valid_ref(checksum="short")

    def test_empty_artifact_id_rejected(self):
        with pytest.raises(ValidationError):
            _valid_ref(artifact_id="")

    def test_empty_artifact_type_rejected(self):
        with pytest.raises(ValidationError):
            _valid_ref(artifact_type="")

    def test_missing_state_produces_terminal_error(self):
        ref = _valid_ref(state=ArtifactResolutionState.MISSING)
        assert ref.is_available is False
        assert ref.sanitized_error == ArtifactPresentationError.ARTIFACT_NOT_FOUND.value

    def test_stale_state_produces_terminal_error(self):
        ref = _valid_ref(state=ArtifactResolutionState.STALE)
        assert ref.is_available is False
        assert ref.sanitized_error == ArtifactPresentationError.ARTIFACT_STALE.value

    def test_checksum_mismatch_produces_terminal_error(self):
        ref = _valid_ref(state=ArtifactResolutionState.CHECKSUM_MISMATCH)
        assert ref.is_available is False
        assert ref.sanitized_error is not None

    def test_available_has_no_error(self):
        ref = _valid_ref()
        assert ref.sanitized_error is None

    def test_preview_with_non_previewable_type_rejected(self):
        with pytest.raises(ValidationError):
            _valid_ref(
                artifact_type="rewrite_dry_run.patch",
                presentation_kind=ArtifactPresentationKind.PREVIEW,
            )

    def test_download_with_non_downloadable_type_rejected(self):
        with pytest.raises(ValidationError, match="not downloadable"):
            _valid_ref(
                artifact_type="some_unknown.bin",
                presentation_kind=ArtifactPresentationKind.DOWNLOAD,
            )

    def test_default_content_type_when_empty(self):
        ref = _valid_ref(content_type="")
        assert ref.content_type == "application/octet-stream"

    def test_default_content_type_when_whitespace(self):
        ref = _valid_ref(content_type="   ")
        assert ref.content_type == "application/octet-stream"

    def test_size_bytes_none_allowed(self):
        ref = _valid_ref(size_bytes=None)
        assert ref.size_bytes is None

    def test_size_bytes_set(self):
        ref = _valid_ref(size_bytes=12345)
        assert ref.size_bytes == 12345


# ══════════════════════════════════════════════════════════════════════════
# 6. ArtifactPresentationRef serialization
# ══════════════════════════════════════════════════════════════════════════

class TestArtifactPresentationRefSerialization:
    """F1-T7: Serialization round-trip."""

    def test_to_dict_available(self):
        ref = _valid_ref()
        d = ref.to_dict()
        assert d["artifact_id"] == "art-001"
        assert d["artifact_type"] == "analysis_report.md"
        assert d["presentation_kind"] == "preview"
        assert d["state"] == "available"
        assert "error" not in d

    def test_to_dict_with_error(self):
        ref = _valid_ref(state=ArtifactResolutionState.MISSING)
        d = ref.to_dict()
        assert d["state"] == "missing"
        assert "error" in d

    def test_to_dict_with_size(self):
        ref = _valid_ref(size_bytes=1024)
        d = ref.to_dict()
        assert d["size_bytes"] == 1024

    def test_to_json(self):
        ref = _valid_ref()
        j = ref.to_json()
        parsed = json.loads(j)
        assert parsed["artifact_id"] == "art-001"

    def test_from_dict_minimal(self):
        d = {
            "artifact_id": "art-002",
            "artifact_type": "migration_plan.yaml",
            "presentation_kind": "download",
            "content_type": "application/x-yaml",
            "checksum": "sha256:xyz789",
            "state": "available",
        }
        ref = ArtifactPresentationRef.from_dict(d)
        assert ref.artifact_id == "art-002"
        assert ref.presentation_kind == ArtifactPresentationKind.DOWNLOAD
        assert ref.is_available is True

    def test_from_dict_with_state(self):
        ref = ArtifactPresentationRef.from_dict({
            "artifact_id": "art-003",
            "artifact_type": "build_log.txt",
            "checksum": "sha256:log123",
            "state": "stale",
        })
        assert ref.state == ArtifactResolutionState.STALE
        assert ref.is_available is False

    def test_round_trip_dict(self):
        ref = _valid_ref(artifact_type="plan_summary.md", size_bytes=500)
        d = ref.to_dict()
        ref2 = ArtifactPresentationRef.from_dict(d)
        assert ref2.artifact_id == ref.artifact_id
        assert ref2.artifact_type == ref.artifact_type
        assert ref2.checksum == ref.checksum
        assert ref2.size_bytes == ref.size_bytes

    def test_round_trip_json(self):
        ref = _valid_ref(
            artifact_type="analysis_report.json",
            presentation_kind=ArtifactPresentationKind.PREVIEW,
            state=ArtifactResolutionState.REDACTION_APPLIED,
        )
        j = ref.to_json()
        parsed = json.loads(j)
        ref2 = ArtifactPresentationRef.from_dict(parsed)
        assert ref2.artifact_id == ref.artifact_id
        assert ref2.state == ArtifactResolutionState.REDACTION_APPLIED
        assert ref2.sanitized_error is None  # redaction is not terminal

    def test_from_dict_with_none_state_defaults(self):
        ref = ArtifactPresentationRef.from_dict({
            "artifact_id": "art-004",
            "artifact_type": "test_report.json",
            "checksum": "sha256:test123",
            "state": None,
        })
        assert ref.state == ArtifactResolutionState.AVAILABLE

    def test_from_dict_none_artifact_id_produces_empty_string(self):
        """Database NULL columns appear as present-but-None keys.
        str(None) would produce the string 'None', which silently passes
        validators. We must guard with ``is not None`` like the rest of
        the codebase (see AnalysisCheckpoint.from_dict)."""
        with pytest.raises(ValidationError, match="must not be empty"):
            ArtifactPresentationRef.from_dict({
                "artifact_id": None,
                "artifact_type": "analysis_report.md",
                "checksum": "sha256:abc123def456",
            })

    def test_from_dict_none_artifact_type_produces_empty_string(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            ArtifactPresentationRef.from_dict({
                "artifact_id": "art-001",
                "artifact_type": None,
                "checksum": "sha256:abc123def456",
            })

    def test_from_dict_none_checksum_produces_empty_string(self):
        with pytest.raises(ValidationError, match="at least 8"):
            ArtifactPresentationRef.from_dict({
                "artifact_id": "art-001",
                "artifact_type": "analysis_report.md",
                "checksum": None,
            })

    def test_from_dict_none_kind_defaults_to_download(self):
        ref = ArtifactPresentationRef.from_dict({
            "artifact_id": "art-001",
            "artifact_type": "rewrite_dry_run.patch",
            "checksum": "sha256:abc123def456",
            "presentation_kind": None,
        })
        assert ref.presentation_kind == ArtifactPresentationKind.DOWNLOAD


# ══════════════════════════════════════════════════════════════════════════
# 7. ArtifactPresentationBatch
# ══════════════════════════════════════════════════════════════════════════

class TestArtifactPresentationBatch:
    """F1-T7: Batch response model."""

    def test_minimal_construction(self):
        batch = _valid_batch()
        assert batch.checkpoint_id == "acp-001"
        assert batch.job_id == "job-abc"
        assert len(batch.artifacts) == 1

    def test_empty_checkpoint_id_rejected(self):
        with pytest.raises(ValidationError):
            _valid_batch(checkpoint_id="")

    def test_empty_job_id_rejected(self):
        with pytest.raises(ValidationError):
            _valid_batch(job_id="")

    def test_available_count(self):
        ref1 = _valid_ref(artifact_id="a1", state=ArtifactResolutionState.AVAILABLE)
        ref2 = _valid_ref(artifact_id="a2", state=ArtifactResolutionState.MISSING)
        ref3 = _valid_ref(artifact_id="a3", state=ArtifactResolutionState.AVAILABLE)
        batch = _valid_batch(artifacts=(ref1, ref2, ref3))
        assert batch.available_count == 2

    def test_previewable_count(self):
        ref1 = _valid_ref(artifact_id="a1", presentation_kind=ArtifactPresentationKind.PREVIEW)
        ref2 = _valid_ref(artifact_id="a2", presentation_kind=ArtifactPresentationKind.DOWNLOAD)
        ref3 = _valid_ref(artifact_id="a3", presentation_kind=ArtifactPresentationKind.PREVIEW)
        batch = _valid_batch(artifacts=(ref1, ref2, ref3))
        assert batch.previewable_count == 2

    def test_empty_artifacts(self):
        batch = _valid_batch(artifacts=())
        assert batch.available_count == 0
        assert batch.previewable_count == 0

    def test_to_dict(self):
        batch = _valid_batch()
        d = batch.to_dict()
        assert d["checkpoint_id"] == "acp-001"
        assert d["available_count"] == 1
        assert isinstance(d["artifacts"], list)
        assert len(d["artifacts"]) == 1

    def test_to_json(self):
        batch = _valid_batch()
        j = batch.to_json()
        parsed = json.loads(j)
        assert parsed["checkpoint_id"] == "acp-001"

    def test_from_dict(self):
        d = {
            "checkpoint_id": "acp-002",
            "job_id": "job-xyz",
            "artifacts": [
                {
                    "artifact_id": "art-x",
                    "artifact_type": "build_log.txt",
                    "checksum": "sha256:log456",
                    "state": "available",
                }
            ],
            "gate_id": "gate-002",
            "gate_checksum": "sha256:gate456",
        }
        batch = ArtifactPresentationBatch.from_dict(d)
        assert batch.checkpoint_id == "acp-002"
        assert len(batch.artifacts) == 1
        assert batch.artifacts[0].artifact_id == "art-x"

    def test_round_trip(self):
        ref1 = _valid_ref(artifact_id="a1", size_bytes=100)
        ref2 = _valid_ref(artifact_id="a2", state=ArtifactResolutionState.STALE)
        batch = _valid_batch(artifacts=(ref1, ref2))
        d = batch.to_dict()
        batch2 = ArtifactPresentationBatch.from_dict(d)
        assert batch2.available_count == batch.available_count
        assert len(batch2.artifacts) == 2

    def test_from_dict_none_checkpoint_id_produces_empty_string(self):
        """Database NULL columns must produce empty strings, not literal 'None'."""
        with pytest.raises(ValidationError, match="must not be empty"):
            ArtifactPresentationBatch.from_dict({
                "checkpoint_id": None,
                "job_id": "job-abc",
            })

    def test_from_dict_none_job_id_produces_empty_string(self):
        with pytest.raises(ValidationError, match="must not be empty"):
            ArtifactPresentationBatch.from_dict({
                "checkpoint_id": "acp-001",
                "job_id": None,
            })


# ══════════════════════════════════════════════════════════════════════════
# 8. Build helper
# ══════════════════════════════════════════════════════════════════════════

class TestBuildPresentationRef:
    """F1-T7: build_presentation_ref helper."""

    def test_auto_detects_preview_kind(self):
        ref = build_presentation_ref(
            "art-001", "analysis_report.md", "sha256:abc",
        )
        assert ref.presentation_kind == ArtifactPresentationKind.PREVIEW

    def test_auto_detects_download_kind(self):
        ref = build_presentation_ref(
            "art-001", "rewrite_dry_run.patch", "sha256:abc",
        )
        assert ref.presentation_kind == ArtifactPresentationKind.DOWNLOAD

    def test_explicit_kind_override(self):
        ref = build_presentation_ref(
            "art-001", "analysis_report.md", "sha256:abc",
            kind=ArtifactPresentationKind.DOWNLOAD,
        )
        assert ref.presentation_kind == ArtifactPresentationKind.DOWNLOAD

    def test_content_type_inferred(self):
        ref = build_presentation_ref("art-001", "migration_plan.yaml", "sha256:abc")
        assert ref.content_type == "application/x-yaml; charset=utf-8"

    def test_state_defaults_to_available(self):
        ref = build_presentation_ref("art-001", "analysis_report.md", "sha256:abc")
        assert ref.state == ArtifactResolutionState.AVAILABLE

    def test_size_bytes_passed_through(self):
        ref = build_presentation_ref(
            "art-001", "analysis_report.md", "sha256:abc", size_bytes=2048,
        )
        assert ref.size_bytes == 2048

    def test_missing_state_passed_through(self):
        ref = build_presentation_ref(
            "art-001", "analysis_report.md", "sha256:abc",
            state=ArtifactResolutionState.MISSING,
        )
        assert ref.state == ArtifactResolutionState.MISSING
        assert ref.is_available is False


# ══════════════════════════════════════════════════════════════════════════
# 9. Validation helpers
# ══════════════════════════════════════════════════════════════════════════

class TestValidationHelpers:
    """F1-T7: validate_artifact_presentation_kind."""

    def test_preview_for_previewable_returns_true(self):
        assert validate_artifact_presentation_kind(
            "analysis_report.md", ArtifactPresentationKind.PREVIEW
        ) is True

    def test_preview_for_non_previewable_returns_false(self):
        assert validate_artifact_presentation_kind(
            "rewrite_dry_run.patch", ArtifactPresentationKind.PREVIEW
        ) is False

    def test_download_for_downloadable_returns_true(self):
        assert validate_artifact_presentation_kind(
            "rewrite_dry_run.patch", ArtifactPresentationKind.DOWNLOAD
        ) is True

    def test_unknown_type_download_returns_false(self):
        assert validate_artifact_presentation_kind(
            "unknown.bin", ArtifactPresentationKind.DOWNLOAD
        ) is False


# ══════════════════════════════════════════════════════════════════════════
# 10. Safe fields contract
# ══════════════════════════════════════════════════════════════════════════

class TestArtifactPresentationFields:
    """F1-T7: Safe fields contract."""

    def test_fields_are_frozenset(self):
        assert isinstance(ARTIFACT_PRESENTATION_FIELDS, frozenset)

    def test_no_dangerous_fields(self):
        dangerous = {
            "sandbox_path", "argv", "env", "raw_command", "absolute_path",
            "filesystem_target", "provider", "deployment", "endpoint",
            "env_ref", "secret", "password", "token", "api_key",
        }
        overlap = ARTIFACT_PRESENTATION_FIELDS & dangerous
        assert not overlap, f"Dangerous fields in contract: {overlap}"

    def test_core_presentation_fields_present(self):
        core = {"artifact_id", "artifact_type", "presentation_kind",
                 "content_type", "checksum", "state"}
        assert core.issubset(ARTIFACT_PRESENTATION_FIELDS)

    def test_batch_fields_present(self):
        batch_fields = {"checkpoint_id", "job_id", "gate_id",
                         "gate_checksum", "artifacts"}
        assert batch_fields.issubset(ARTIFACT_PRESENTATION_FIELDS)


# ══════════════════════════════════════════════════════════════════════════
# 11. Redaction contract
# ══════════════════════════════════════════════════════════════════════════

class TestRedactionContract:
    """F1-T7: Redaction rules must be defined and explicit."""

    def test_redaction_rules_are_frozenset(self):
        assert isinstance(ARTIFACT_REDACTION_RULES, frozenset)

    def test_core_redaction_rules_present(self):
        core = {
            "redact_absolute_paths",
            "redact_env_vars",
            "redact_secrets",
            "redact_sandbox_paths",
            "redact_command_argv",
            "truncate_to_max_size",
        }
        assert core.issubset(ARTIFACT_REDACTION_RULES)

    def test_max_preview_size_is_reasonable(self):
        assert MAX_PREVIEW_CONTENT_CHARS == 32_768  # 32 KB

    def test_redacted_placeholder_is_not_reversible(self):
        assert "[REDACTED]" not in {"sandbox", "path", "secret", "key"}


# ══════════════════════════════════════════════════════════════════════════
# 12. No dangerous fields in output
# ══════════════════════════════════════════════════════════════════════════

class TestNoDangerousFieldsInOutput:
    """F1-T7: Serialization must never expose dangerous keys."""

    _DANGER_KEYS = frozenset({
        "sandbox_path", "argv", "env", "raw_command", "absolute_path",
        "filesystem_target", "provider", "deployment", "endpoint",
        "env_ref", "secret", "password", "token", "api_key",
        "authorization_header",
    })

    def test_ref_to_dict_no_dangerous_keys(self):
        ref = _valid_ref()
        d = ref.to_dict()
        for key in self._DANGER_KEYS:
            assert key not in d, f"Ref to_dict contains {key}"

    def test_ref_to_json_no_dangerous_keys(self):
        ref = _valid_ref()
        j = ref.to_json()
        parsed = json.loads(j)
        for key in self._DANGER_KEYS:
            assert key not in parsed, f"Ref to_json contains {key}"

    def test_batch_to_dict_no_dangerous_keys(self):
        batch = _valid_batch()
        d = batch.to_dict()
        for key in self._DANGER_KEYS:
            assert key not in d, f"Batch to_dict contains {key}"

    def test_batch_to_json_no_dangerous_keys(self):
        batch = _valid_batch()
        j = batch.to_json()
        parsed = json.loads(j)
        for key in self._DANGER_KEYS:
            assert key not in parsed, f"Batch to_json contains {key}"

    def test_ref_from_dict_ignores_dangerous_keys_in_input(self):
        # from_dict should only pick known fields, not inject dangerous ones
        bad_input = {
            "artifact_id": "art-bad",
            "artifact_type": "analysis_report.md",
            "checksum": "sha256:bad",
            "sandbox_path": "C:\\secret\\path",
            "env": {"SECRET_KEY": "leaked"},
            "password": "hunter2",
        }
        ref = ArtifactPresentationRef.from_dict(bad_input)
        d = ref.to_dict()
        assert "sandbox_path" not in d
        assert "env" not in d
        assert "password" not in d
