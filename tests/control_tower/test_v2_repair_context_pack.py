"""F5-T2: Repair context pack — focused tests for repair context pack builders.

Covers:
  - build_repair_context_pack from FailureEvidence
  - compute_context_pack_checksum volatility
  - compute_base_repo_state_checksum volatility
  - is_context_pack_stale detection
  - context_pack_to_dict safe export
  - Sort invariants for prior proposal checksums and revision IDs
  - Cycle number / max_cycles propagation
  - Failure evidence checksum inclusion
  - Forbidden key redaction
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from migration_factory.control_tower.domain.checksums import sha256_canonical_json
from migration_factory.repair_loop.failure_evidence import (
    FailureEvidence,
    FailureSource,
    build_failure_evidence,
)
from migration_factory.repair_loop.repair_context import (
    FORBIDDEN_CONTEXT_KEYS,
    RepairContextPack,
    build_repair_context_pack,
    compute_base_repo_state_checksum,
    compute_context_pack_checksum,
    context_pack_to_dict,
    is_context_pack_stale,
)


# ── Helpers ───────────────────────────────────────────────────────────


def _sample_failure_evidence(**overrides: object) -> FailureEvidence:
    kwargs: dict = {
        "failure_source": FailureSource.BUILD,
        "stage_index": 1,
        "job_id": "job-001",
        "command_id": "cmd-build-01",
        "failure_summary": "Compilation failed: cannot find symbol",
        "changed_files": ("pom.xml", "src/main/java/App.java"),
        "source_profile": "java-8",
        "target_profile": "java-17",
        "accepted_artifact_checksums": ("abc123", "def456"),
        "safe_log_preview": "[ERROR] /app/src/main/java/LegacyUtil.java:[12,34]",
    }
    kwargs.update(overrides)  # type: ignore[arg-type]
    return build_failure_evidence(**kwargs)  # type: ignore[arg-type]


def _make_pack(**overrides: object) -> RepairContextPack:
    evidence = _sample_failure_evidence()
    kwargs: dict = {
        "failure_evidence": evidence,
        "prior_proposal_checksums": ("zzz", "aaa", "mmm"),
        "prior_revision_ids": ("rev-3", "rev-1", "rev-2"),
        "user_comments": "",
        "cycle_number": 1,
        "max_cycles": 5,
    }
    kwargs.update(overrides)  # type: ignore[arg-type]
    return build_repair_context_pack(**kwargs)  # type: ignore[arg-type]


# ── 1. Build context pack from failure evidence — verify all fields
#    populated ───────────────────────────────────────────────────────


class TestBuildContextPackFromFailureEvidence:
    def test_all_fields_populated(self) -> None:
        evidence = _sample_failure_evidence()
        pack = build_repair_context_pack(
            failure_evidence=evidence,
            job_id="",
            stage_index=0,
            command_id="",
        )

        assert pack.job_id == "job-001"
        assert pack.stage_index == 1
        assert pack.command_id == "cmd-build-01"
        assert pack.failure_source == "build"
        assert pack.failure_evidence_checksum == evidence.content_checksum
        assert pack.source_profile == "java-8"
        assert pack.target_profile == "java-17"
        assert pack.changed_files == ("pom.xml", "src/main/java/App.java")
        assert pack.safe_log_preview == evidence.safe_log_preview
        assert pack.base_repo_state_checksum
        assert pack.context_pack_checksum
        assert pack.created_at
        assert pack.schema_version == "1.0.0"

    def test_job_id_falls_back_to_evidence(self) -> None:
        evidence = _sample_failure_evidence(job_id="ev-job")
        pack = build_repair_context_pack(failure_evidence=evidence)
        assert pack.job_id == "ev-job"

    def test_job_id_override_explicit(self) -> None:
        evidence = _sample_failure_evidence(job_id="ev-job")
        pack = build_repair_context_pack(
            failure_evidence=evidence, job_id="explicit-job"
        )
        assert pack.job_id == "explicit-job"

    def test_stage_index_falls_back_to_evidence(self) -> None:
        evidence = _sample_failure_evidence(stage_index=2)
        pack = build_repair_context_pack(failure_evidence=evidence)
        assert pack.stage_index == 2

    def test_stage_index_override_explicit(self) -> None:
        evidence = _sample_failure_evidence(stage_index=2)
        pack = build_repair_context_pack(
            failure_evidence=evidence, stage_index=3
        )
        assert pack.stage_index == 3

    def test_command_id_falls_back_to_evidence(self) -> None:
        evidence = _sample_failure_evidence(command_id="ev-cmd")
        pack = build_repair_context_pack(failure_evidence=evidence)
        assert pack.command_id == "ev-cmd"

    def test_command_id_override_explicit(self) -> None:
        evidence = _sample_failure_evidence(command_id="ev-cmd")
        pack = build_repair_context_pack(
            failure_evidence=evidence, command_id="my-cmd"
        )
        assert pack.command_id == "my-cmd"

    def test_changed_files_from_evidence(self) -> None:
        evidence = _sample_failure_evidence(
            changed_files=("B.java", "A.java")
        )
        pack = build_repair_context_pack(failure_evidence=evidence)
        assert pack.changed_files == ("A.java", "B.java")

    def test_changed_files_override_explicit(self) -> None:
        evidence = _sample_failure_evidence(
            changed_files=("old.java",)
        )
        pack = build_repair_context_pack(
            failure_evidence=evidence, changed_files=("new.java",)
        )
        assert pack.changed_files == ("new.java",)

    def test_source_target_profile_from_evidence(self) -> None:
        evidence = _sample_failure_evidence(
            source_profile="ev-src", target_profile="ev-tgt"
        )
        pack = build_repair_context_pack(failure_evidence=evidence)
        assert pack.source_profile == "ev-src"
        assert pack.target_profile == "ev-tgt"

    def test_source_target_profile_override_explicit(self) -> None:
        evidence = _sample_failure_evidence(
            source_profile="ev-src", target_profile="ev-tgt"
        )
        pack = build_repair_context_pack(
            failure_evidence=evidence,
            source_profile="my-src",
            target_profile="my-tgt",
        )
        assert pack.source_profile == "my-src"
        assert pack.target_profile == "my-tgt"

    def test_accepted_analysis_and_planning_checksums(self) -> None:
        evidence = _sample_failure_evidence()
        pack = build_repair_context_pack(
            failure_evidence=evidence,
            accepted_analysis_checksum="analysis_cs",
            accepted_planning_checksum="planning_cs",
        )
        assert pack.accepted_analysis_checksum == "analysis_cs"
        assert pack.accepted_planning_checksum == "planning_cs"

    def test_prior_reviewer_notes_preserved(self) -> None:
        evidence = _sample_failure_evidence()
        pack = build_repair_context_pack(
            failure_evidence=evidence,
            prior_reviewer_notes=("Note A", "Note B"),
        )
        assert pack.prior_reviewer_notes == ("Note A", "Note B")

    def test_safe_log_preview_from_evidence(self) -> None:
        evidence = _sample_failure_evidence(
            safe_log_preview="[WARN] deprecation"
        )
        pack = build_repair_context_pack(failure_evidence=evidence)
        assert pack.safe_log_preview == "[WARN] deprecation"

    def test_file_checksums_flow_into_base_repo_checksum(self) -> None:
        evidence = _sample_failure_evidence()
        file_checksums = {"pom.xml": "h1", "App.java": "h2"}
        pack = build_repair_context_pack(
            failure_evidence=evidence, file_checksums=file_checksums
        )
        assert pack.base_repo_state_checksum
        assert pack.base_repo_state_checksum != compute_base_repo_state_checksum(
            changed_files=pack.changed_files,
        )


# ── 2. Context pack checksum changes when user_comments change ────────


class TestContextPackChecksumUserComments:
    def test_user_comments_change_checksum(self) -> None:
        pack_a = _make_pack(user_comments="initial comment")
        pack_b = _make_pack(user_comments="revised comment")
        assert pack_a.context_pack_checksum != pack_b.context_pack_checksum

    def test_empty_to_nonempty_comments_change_checksum(self) -> None:
        pack_a = _make_pack(user_comments="")
        pack_b = _make_pack(user_comments="user feedback")
        assert pack_a.context_pack_checksum != pack_b.context_pack_checksum


# ── 3. Context pack checksum changes when changed_files change ─────────


class TestContextPackChecksumChangedFiles:
    def test_changed_files_change_checksum(self) -> None:
        pack_a = _make_pack(changed_files=("a.txt",))
        pack_b = _make_pack(changed_files=("b.txt",))
        assert pack_a.context_pack_checksum != pack_b.context_pack_checksum

    def test_changed_files_order_independent(self) -> None:
        pack_a = _make_pack(changed_files=("z.txt", "a.txt"))
        pack_b = _make_pack(changed_files=("a.txt", "z.txt"))
        assert pack_a.context_pack_checksum == pack_b.context_pack_checksum


# ── 4. Context pack checksum changes when accepted artifact checksums
#    change ─────────────────────────────────────────────────────────


class TestContextPackChecksumAcceptedArtifacts:
    def test_accepted_artifact_checksums_change_checksum(self) -> None:
        evidence_a = _sample_failure_evidence(
            accepted_artifact_checksums=("cs-a",)
        )
        evidence_b = _sample_failure_evidence(
            accepted_artifact_checksums=("cs-b",)
        )
        pack_a = build_repair_context_pack(failure_evidence=evidence_a)
        pack_b = build_repair_context_pack(failure_evidence=evidence_b)
        assert pack_a.context_pack_checksum != pack_b.context_pack_checksum


# ── 5. Base repo state checksum changes when file_checksums change ─────


class TestBaseRepoStateChecksumFileChecksums:
    def test_file_checksums_change_base_checksum(self) -> None:
        cs_a = compute_base_repo_state_checksum(
            changed_files=("f.py",),
            file_checksums={"f.py": "hash1"},
        )
        cs_b = compute_base_repo_state_checksum(
            changed_files=("f.py",),
            file_checksums={"f.py": "hash2"},
        )
        assert cs_a != cs_b

    def test_file_checksums_added_removed(self) -> None:
        cs_a = compute_base_repo_state_checksum(
            changed_files=("x.py", "y.py"),
            file_checksums={"x.py": "h1"},
        )
        cs_b = compute_base_repo_state_checksum(
            changed_files=("x.py", "y.py"),
            file_checksums={"x.py": "h1", "y.py": "h2"},
        )
        assert cs_a != cs_b

    def test_empty_file_checksums_stable(self) -> None:
        cs_a = compute_base_repo_state_checksum(
            changed_files=("mod.py",),
        )
        cs_b = compute_base_repo_state_checksum(
            changed_files=("mod.py",),
        )
        assert cs_a == cs_b


# ── 6. Base repo state checksum changes when changed_files change ──────


class TestBaseRepoStateChecksumChangedFiles:
    def test_changed_files_change_base_checksum(self) -> None:
        cs_a = compute_base_repo_state_checksum(changed_files=("src/a.py",))
        cs_b = compute_base_repo_state_checksum(changed_files=("src/b.py",))
        assert cs_a != cs_b

    def test_changed_files_addition(self) -> None:
        cs_a = compute_base_repo_state_checksum(changed_files=("one.py",))
        cs_b = compute_base_repo_state_checksum(
            changed_files=("one.py", "two.py")
        )
        assert cs_a != cs_b

    def test_changed_files_order_independent(self) -> None:
        cs_a = compute_base_repo_state_checksum(
            changed_files=("b.py", "a.py")
        )
        cs_b = compute_base_repo_state_checksum(
            changed_files=("a.py", "b.py")
        )
        assert cs_a == cs_b

    def test_accepted_artifact_checksums_influence(self) -> None:
        cs_a = compute_base_repo_state_checksum(
            accepted_artifact_checksums=("art-a",)
        )
        cs_b = compute_base_repo_state_checksum(
            accepted_artifact_checksums=("art-b",)
        )
        assert cs_a != cs_b

    def test_source_target_profile_influence(self) -> None:
        cs_a = compute_base_repo_state_checksum(
            source_profile="java-8", target_profile="java-11"
        )
        cs_b = compute_base_repo_state_checksum(
            source_profile="java-8", target_profile="java-17"
        )
        assert cs_a != cs_b


# ── 7. is_context_pack_stale returns True when file checksums differ ───


class TestIsContextPackStaleTrue:
    def test_stale_when_file_checksums_differ(self) -> None:
        evidence = _sample_failure_evidence()
        pack = build_repair_context_pack(
            failure_evidence=evidence,
            file_checksums={"f.txt": "hash1"},
        )
        stale = is_context_pack_stale(
            pack, current_file_checksums={"f.txt": "hash2"}
        )
        assert stale is True

    def test_stale_when_new_file_added(self) -> None:
        evidence = _sample_failure_evidence()
        pack = build_repair_context_pack(
            failure_evidence=evidence,
            file_checksums={"a.txt": "h1"},
        )
        stale = is_context_pack_stale(
            pack,
            current_file_checksums={"a.txt": "h1", "b.txt": "h2"},
        )
        assert stale is True

    def test_stale_when_accepted_artifact_differs(self) -> None:
        evidence_a = _sample_failure_evidence(
            accepted_artifact_checksums=("old",)
        )
        pack = build_repair_context_pack(failure_evidence=evidence_a)
        stale = is_context_pack_stale(
            pack, current_accepted_artifact_checksums=("new",)
        )
        assert stale is True


# ── 8. is_context_pack_stale returns False when same state ─────────────


class TestIsContextPackStaleFalse:
    def test_not_stale_when_same_file_checksums_and_artifacts(self) -> None:
        evidence = _sample_failure_evidence(accepted_artifact_checksums=("art1",))
        pack = build_repair_context_pack(
            failure_evidence=evidence,
            file_checksums={"x.txt": "hashX"},
        )
        stale = is_context_pack_stale(
            pack,
            current_file_checksums={"x.txt": "hashX"},
            current_accepted_artifact_checksums=("art1",),
        )
        assert stale is False

    def test_not_stale_when_no_file_checksums_empty_artifacts(self) -> None:
        evidence = _sample_failure_evidence(accepted_artifact_checksums=())
        pack = build_repair_context_pack(failure_evidence=evidence)
        stale = is_context_pack_stale(pack)
        assert stale is False

    def test_not_stale_when_same_accepted_artifacts(self) -> None:
        evidence = _sample_failure_evidence(accepted_artifact_checksums=("cs",))
        pack = build_repair_context_pack(failure_evidence=evidence)
        stale = is_context_pack_stale(
            pack, current_accepted_artifact_checksums=("cs",)
        )
        assert stale is False


# ── 9. Prior proposal checksums are sorted in context ──────────────────


class TestPriorProposalChecksumsSorted:
    def test_prior_proposal_checksums_sorted_in_pack(self) -> None:
        pack = _make_pack(
            prior_proposal_checksums=("zzz", "aaa", "mmm")
        )
        assert pack.prior_proposal_checksums == ("aaa", "mmm", "zzz")

    def test_prior_proposal_checksums_sorted_in_checksum_payload(self) -> None:
        pack_a = _make_pack(
            prior_proposal_checksums=("c", "b", "a")
        )
        pack_b = _make_pack(
            prior_proposal_checksums=("a", "c", "b")
        )
        assert pack_a.context_pack_checksum == pack_b.context_pack_checksum

    def test_single_prior_proposal(self) -> None:
        pack = _make_pack(prior_proposal_checksums=("single",))
        assert pack.prior_proposal_checksums == ("single",)

    def test_empty_prior_proposals(self) -> None:
        evidence = _sample_failure_evidence()
        pack = build_repair_context_pack(failure_evidence=evidence)
        assert pack.prior_proposal_checksums == ()


# ── 10. Prior revision IDs are sorted ──────────────────────────────────


class TestPriorRevisionIdsSorted:
    def test_prior_revision_ids_sorted_in_pack(self) -> None:
        pack = _make_pack(
            prior_revision_ids=("rev-3", "rev-1", "rev-2")
        )
        assert pack.prior_revision_ids == ("rev-1", "rev-2", "rev-3")

    def test_prior_revision_ids_sorted_in_checksum(self) -> None:
        pack_a = _make_pack(prior_revision_ids=("z", "a"))
        pack_b = _make_pack(prior_revision_ids=("a", "z"))
        assert pack_a.context_pack_checksum == pack_b.context_pack_checksum

    def test_single_revision_id(self) -> None:
        pack = _make_pack(prior_revision_ids=("rev-100",))
        assert pack.prior_revision_ids == ("rev-100",)

    def test_empty_revision_ids(self) -> None:
        evidence = _sample_failure_evidence()
        pack = build_repair_context_pack(failure_evidence=evidence)
        assert pack.prior_revision_ids == ()


# ── 11. context_pack_to_dict returns safe dict ─────────────────────────


class TestContextPackToDict:
    def test_returns_dict_with_expected_keys(self) -> None:
        pack = _make_pack()
        result = context_pack_to_dict(pack)
        assert isinstance(result, dict)
        expected_keys = {
            "job_id",
            "stage_index",
            "command_id",
            "failure_source",
            "failure_evidence_checksum",
            "source_profile",
            "target_profile",
            "accepted_analysis_checksum",
            "accepted_planning_checksum",
            "prior_proposal_checksums",
            "prior_reviewer_notes",
            "user_comments",
            "changed_files",
            "safe_log_preview",
            "base_repo_state_checksum",
            "context_pack_checksum",
            "prior_revision_ids",
            "cycle_number",
            "max_cycles",
            "created_at",
            "schema_version",
        }
        assert set(result.keys()) == expected_keys

    def test_dict_values_match_pack(self) -> None:
        pack = _make_pack(user_comments="hello")
        result = context_pack_to_dict(pack)
        assert result["job_id"] == pack.job_id
        assert result["user_comments"] == "hello"
        assert result["cycle_number"] == pack.cycle_number
        assert result["max_cycles"] == pack.max_cycles
        assert result["prior_proposal_checksums"] == list(
            pack.prior_proposal_checksums
        )
        assert result["prior_revision_ids"] == list(pack.prior_revision_ids)
        assert result["changed_files"] == list(pack.changed_files)

    def test_prior_proposal_checksums_are_list_in_dict(self) -> None:
        pack = _make_pack(
            prior_proposal_checksums=("cs1", "cs2")
        )
        result = context_pack_to_dict(pack)
        assert result["prior_proposal_checksums"] == ["cs1", "cs2"]
        assert isinstance(result["prior_proposal_checksums"], list)

    def test_changed_files_are_list_in_dict(self) -> None:
        pack = _make_pack(changed_files=("a.py", "b.py"))
        result = context_pack_to_dict(pack)
        assert result["changed_files"] == ["a.py", "b.py"]
        assert isinstance(result["changed_files"], list)

    def test_created_at_present_in_dict(self) -> None:
        pack = _make_pack()
        result = context_pack_to_dict(pack)
        assert result["created_at"] == pack.created_at
        assert isinstance(result["created_at"], str)
        assert len(result["created_at"]) > 0


# ── 12. User comments appear in context pack ───────────────────────────


class TestUserCommentsInPack:
    def test_user_comments_stored(self) -> None:
        pack = _make_pack(user_comments="Please fix the import error")
        assert pack.user_comments == "Please fix the import error"

    def test_user_comments_in_dict(self) -> None:
        pack = _make_pack(user_comments="review notes")
        result = context_pack_to_dict(pack)
        assert result["user_comments"] == "review notes"

    def test_user_comments_in_checksum_payload(self) -> None:
        pack_a = _make_pack(user_comments="note A")
        pack_b = _make_pack(user_comments="note B")
        assert pack_a.context_pack_checksum != pack_b.context_pack_checksum


# ── 13. Cycle number and max_cycles are propagated ─────────────────────


class TestCycleNumberAndMaxCycles:
    def test_cycle_number_propagated(self) -> None:
        pack = _make_pack(cycle_number=4)
        assert pack.cycle_number == 4

    def test_max_cycles_propagated(self) -> None:
        pack = _make_pack(max_cycles=7)
        assert pack.max_cycles == 7

    def test_default_values(self) -> None:
        evidence = _sample_failure_evidence()
        pack = build_repair_context_pack(failure_evidence=evidence)
        assert pack.cycle_number == 0
        assert pack.max_cycles == 3

    def test_cycle_number_in_dict(self) -> None:
        pack = _make_pack(cycle_number=2, max_cycles=4)
        result = context_pack_to_dict(pack)
        assert result["cycle_number"] == 2
        assert result["max_cycles"] == 4

    def test_cycle_number_affects_checksum(self) -> None:
        pack_a = _make_pack(cycle_number=1)
        pack_b = _make_pack(cycle_number=2)
        assert pack_a.context_pack_checksum != pack_b.context_pack_checksum

    def test_max_cycles_affects_checksum(self) -> None:
        pack_a = _make_pack(max_cycles=3)
        pack_b = _make_pack(max_cycles=5)
        assert pack_a.context_pack_checksum != pack_b.context_pack_checksum


# ── 14. Failure evidence checksum is included in context pack ──────────


class TestFailureEvidenceChecksumIncluded:
    def test_checksum_stored(self) -> None:
        evidence = _sample_failure_evidence()
        pack = build_repair_context_pack(failure_evidence=evidence)
        assert pack.failure_evidence_checksum
        assert pack.failure_evidence_checksum == evidence.content_checksum

    def test_checksum_in_dict(self) -> None:
        pack = _make_pack()
        result = context_pack_to_dict(pack)
        assert result["failure_evidence_checksum"] == pack.failure_evidence_checksum

    def test_checksum_in_checksum_payload(self) -> None:
        evidence = _sample_failure_evidence(
            failure_summary="Summary A"
        )
        pack_a = build_repair_context_pack(failure_evidence=evidence)

        evidence_b = _sample_failure_evidence(
            failure_summary="Summary B"
        )
        pack_b = build_repair_context_pack(failure_evidence=evidence_b)

        assert pack_a.failure_evidence_checksum != pack_b.failure_evidence_checksum
        assert pack_a.context_pack_checksum != pack_b.context_pack_checksum

    def test_same_failure_evidence_yields_same_checksum(self) -> None:
        evidence = _sample_failure_evidence()
        pack_1 = build_repair_context_pack(failure_evidence=evidence)
        pack_2 = build_repair_context_pack(failure_evidence=evidence)
        assert pack_1.failure_evidence_checksum == pack_2.failure_evidence_checksum

    def test_failure_source_propagated(self) -> None:
        build_ev = _sample_failure_evidence(failure_source=FailureSource.BUILD)
        pack = build_repair_context_pack(failure_evidence=build_ev)
        assert pack.failure_source == "build"

        test_ev = _sample_failure_evidence(failure_source=FailureSource.TEST)
        pack = build_repair_context_pack(failure_evidence=test_ev)
        assert pack.failure_source == "test"


# ── 15. Redaction: forbidden keys not leaked ───────────────────────────


class TestForbiddenKeysRedaction:
    def test_forbidden_set_is_defined(self) -> None:
        assert "sandbox_path" in FORBIDDEN_CONTEXT_KEYS
        assert "argv" in FORBIDDEN_CONTEXT_KEYS
        assert "env" in FORBIDDEN_CONTEXT_KEYS
        assert "raw_command" in FORBIDDEN_CONTEXT_KEYS
        assert "endpoint" in FORBIDDEN_CONTEXT_KEYS
        assert "deployment" in FORBIDDEN_CONTEXT_KEYS
        assert "env_ref" in FORBIDDEN_CONTEXT_KEYS
        assert "user_supplied_file_path" in FORBIDDEN_CONTEXT_KEYS
        assert "filesystem_target" in FORBIDDEN_CONTEXT_KEYS

    def test_forbidden_keys_not_in_dataclass_fields(self) -> None:
        pack_fields = {f.name for f in RepairContextPack.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        for forbidden_key in FORBIDDEN_CONTEXT_KEYS:
            assert forbidden_key not in pack_fields, (
                f"Forbidden key {forbidden_key!r} found in RepairContextPack fields"
            )

    def test_context_pack_to_dict_excludes_forbidden_keys(self) -> None:
        pack = _make_pack()
        result = context_pack_to_dict(pack)
        for forbidden_key in FORBIDDEN_CONTEXT_KEYS:
            assert forbidden_key not in result, (
                f"Forbidden key {forbidden_key!r} leaked in context_pack_to_dict"
            )

    def test_built_pack_has_no_forbidden_attrs(self) -> None:
        pack = _make_pack()
        for forbidden_key in FORBIDDEN_CONTEXT_KEYS:
            assert not hasattr(pack, forbidden_key) or not getattr(
                pack, forbidden_key
            ), (
                f"Forbidden key {forbidden_key!r} leaked on RepairContextPack instance"
            )

    def test_validated_forbidden_keys_empty_for_clean_pack(self) -> None:
        from migration_factory.repair_loop.repair_context import (
            _validate_context_forbidden_keys,
        )
        pack = _make_pack()
        failures = _validate_context_forbidden_keys(pack)
        assert failures == []


# ── Edge cases ─────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_evidence_minimal_pack(self) -> None:
        evidence = build_failure_evidence(failure_source=FailureSource.UNKNOWN)
        pack = build_repair_context_pack(failure_evidence=evidence)
        assert pack.job_id == ""
        assert pack.stage_index == 0
        assert pack.command_id == ""
        assert pack.failure_source == "unknown"
        assert pack.context_pack_checksum

    def test_checksum_deterministic_for_same_inputs(self) -> None:
        evidence = _sample_failure_evidence()
        pack_a = build_repair_context_pack(
            failure_evidence=evidence,
            user_comments="same",
            cycle_number=1,
        )
        pack_b = build_repair_context_pack(
            failure_evidence=evidence,
            user_comments="same",
            cycle_number=1,
        )
        assert pack_a.context_pack_checksum == pack_b.context_pack_checksum

    def test_checksum_differs_for_different_profiles(self) -> None:
        evidence_a = _sample_failure_evidence(
            source_profile="java-8", target_profile="java-11"
        )
        evidence_b = _sample_failure_evidence(
            source_profile="java-8", target_profile="java-17"
        )
        pack_a = build_repair_context_pack(failure_evidence=evidence_a)
        pack_b = build_repair_context_pack(failure_evidence=evidence_b)
        assert pack_a.context_pack_checksum != pack_b.context_pack_checksum


# ── Imports sanity ──────────────────────────────────────────────────────


def test_all_expected_exports() -> None:
    import migration_factory.repair_loop.repair_context as mod

    assert hasattr(mod, "RepairContextPack")
    assert hasattr(mod, "FORBIDDEN_CONTEXT_KEYS")
    assert hasattr(mod, "build_repair_context_pack")
    assert hasattr(mod, "compute_context_pack_checksum")
    assert hasattr(mod, "compute_base_repo_state_checksum")
    assert hasattr(mod, "is_context_pack_stale")
    assert hasattr(mod, "context_pack_to_dict")
