"""Focused tests for F2 review-chain contracts.

AMF-254 — Deterministic artifact contract
AMF-255 — Primary LLM role
AMF-256 — Reviewer LLM role
AMF-257 — Reviewer decision matrix
AMF-258 — Final reviewed Markdown artifact schema
AMF-259 — Retry and revision behavior
AMF-260 — Metadata and checksum binding
AMF-261 — Reviewer-required test matrix
"""

from __future__ import annotations

import pytest

from migration_factory.control_tower.application.v2_review_chain_contracts import (
    ArtifactPhase,
    ArtifactRejectionResult,
    ChecksumBindingValidationError,
    ChecksumChainValidationError,
    CompleteChecksumChain,
    DeterministicAnalysisFacts,
    DeterministicArtifactBinding,
    DeterministicPlanningFacts,
    FinalMarkdownMetadata,
    FinalMarkdownSection,
    FinalReviewedMarkdown,
    PrimaryLLMInput,
    PrimaryLLMOutput,
    PrimaryLLMOutputValidationError,
    ReviewDimension,
    ReviewRetryLimits,
    ReviewerDecision,
    ReviewerDecisionOutcome,
    ReviewerLLMInput,
    ReviewerLLMOutput,
    ReviewerValidationResult,
    RevisionRequest,
    RevisionResult,
    RevisionState,
    build_artifact_rejection_result,
    can_produce_final_artifact,
    compute_final_markdown_checksum,
    compute_primary_output_checksum,
    compute_reviewer_output_checksum,
    is_checkpoint_acceptance_blocked,
    is_checksum_stale,
    is_decision_failed_closed,
    is_revision_idempotent,
    resolve_failed_closed_decision,
    resolve_reviewer_decision,
    resolve_reviewer_failed_decision,
    resolve_stale_decision,
    safe_metadata_dict,
    validate_checksum_binding,
    validate_checksum_chain_against_reference,
    validate_complete_checksum_chain,
    validate_deterministic_artifact_binding,
    validate_final_markdown,
    validate_metadata_safety,
    validate_primary_llm_input,
    validate_primary_llm_output,
    validate_reviewed_output_contract,
    validate_reviewer_llm_input,
    validate_reviewer_llm_output,
    validate_revision_request,
)
from migration_factory.control_tower.domain.checksums import sha256_canonical_json


# ── Helpers ─────────────────────────────────────────────────────────────


def _valid_analysis_facts() -> DeterministicAnalysisFacts:
    return DeterministicAnalysisFacts(
        detected_framework="Spring Boot",
        detected_language="Java",
        build_tool="maven",
        source_java_version="8",
        source_spring_boot_version="2.1.6",
        javax_import_count=45,
        has_datasource_config=True,
    )


def _valid_planning_facts() -> DeterministicPlanningFacts:
    return DeterministicPlanningFacts(
        selected_migration_stages=("baseline", "java-17", "spring-boot-3-5-14"),
        included_stages=("baseline", "java-17", "spring-boot-3-5-14"),
        target_java_version="17",
        target_spring_boot_version="3.5.14",
        profile_id="boot2-to-boot3",
        strategy="upgrade",
        executable=True,
        unit_count=3,
    )


def _valid_analysis_binding() -> DeterministicArtifactBinding:
    return DeterministicArtifactBinding(
        artifact_role="deterministic",
        artifact_phase="analysis",
        job_id="job-001",
        stage_index=1,
        artifact_ref="analysis/analysis_report.json",
        content_checksum=sha256_canonical_json({"version": "1.0.0"}),
        input_checksum=sha256_canonical_json({"pom": "checksum1"}),
        deterministic_facts=_valid_analysis_facts(),
        created_at="2025-01-01T00:00:00.000000Z",
    )


def _valid_planning_binding() -> DeterministicArtifactBinding:
    return DeterministicArtifactBinding(
        artifact_role="deterministic",
        artifact_phase="planning",
        job_id="job-001",
        stage_index=2,
        artifact_ref="planning/migration_plan.yaml",
        content_checksum=sha256_canonical_json({"plan": "v1"}),
        input_checksum=sha256_canonical_json({"report": "checksum1"}),
        deterministic_facts=_valid_planning_facts(),
        created_at="2025-01-01T00:00:00.000000Z",
    )


def _valid_primary_input() -> PrimaryLLMInput:
    return PrimaryLLMInput(
        deterministic_artifact_ref="analysis/analysis_report.json",
        deterministic_artifact_checksum=sha256_canonical_json({"version": "1.0.0"}),
        phase="analysis",
        job_id="job-001",
        stage_index=1,
        source_profile={"java": "8", "spring_boot": "2.1.6"},
        target_profile={"java": "17", "spring_boot": "3.5.14"},
    )


def _valid_primary_output() -> PrimaryLLMOutput:
    return PrimaryLLMOutput(
        reasoning="The project uses javax.* imports which require Jakarta migration.",
        risks=("javax-to-jakarta migration complexity",),
        confidence=0.85,
        recommended_next_step="Proceed with Jakarta migration stage.",
        draft_markdown="# Analysis Summary\n\nThis project needs Jakarta migration.",
        machine_readable_metadata={"version": "1.0"},
    )


def _valid_reviewer_input() -> ReviewerLLMInput:
    return ReviewerLLMInput(
        deterministic_artifact_ref="analysis/analysis_report.json",
        deterministic_artifact_checksum=sha256_canonical_json({"version": "1.0.0"}),
        primary_output_ref="primary/analysis_primary_output",
        primary_output_checksum=sha256_canonical_json({"output": "v1"}),
        primary_reasoning="The project uses javax.* imports.",
        draft_markdown="# Analysis Summary",
        phase="analysis",
        job_id="job-001",
        stage_index=1,
    )


def _valid_reviewer_output(deterministic_checksum: str, primary_checksum: str) -> ReviewerLLMOutput:
    return ReviewerLLMOutput(
        decision="accept",
        notes=("Evidence fits the deterministic facts.",),
        confidence=0.9,
        risks=("javax-to-jakarta migration is non-trivial.",),
        policy_concerns=(),
        reviewed_artifact_checksum=deterministic_checksum,
        reviewed_primary_output_checksum=primary_checksum,
    )


# ── AMF-254: Deterministic Artifact Contract ───────────────────────────


class TestDeterministicArtifactContract:
    """Tests for deterministic Analysis and Planning artifact contracts."""

    def test_valid_analysis_binding_passes_validation(self) -> None:
        binding = _valid_analysis_binding()
        failures = validate_deterministic_artifact_binding(binding)
        assert failures == []

    def test_valid_planning_binding_passes_validation(self) -> None:
        binding = _valid_planning_binding()
        failures = validate_deterministic_artifact_binding(binding)
        assert failures == []

    def test_missing_artifact_ref_fails(self) -> None:
        binding = DeterministicArtifactBinding(
            artifact_role="deterministic",
            artifact_phase="analysis",
            job_id="job-001",
            stage_index=1,
            artifact_ref="",
            content_checksum="abc123",
            deterministic_facts=_valid_analysis_facts(),
        )
        failures = validate_deterministic_artifact_binding(binding)
        assert any("missing artifact_ref" in f for f in failures)

    def test_missing_content_checksum_fails(self) -> None:
        binding = DeterministicArtifactBinding(
            artifact_role="deterministic",
            artifact_phase="analysis",
            job_id="job-001",
            stage_index=1,
            artifact_ref="analysis/report.json",
            content_checksum="",
            deterministic_facts=_valid_analysis_facts(),
        )
        failures = validate_deterministic_artifact_binding(binding)
        assert any("missing content_checksum" in f for f in failures)

    def test_missing_job_id_fails(self) -> None:
        binding = DeterministicArtifactBinding(
            artifact_role="deterministic",
            artifact_phase="analysis",
            job_id="",
            stage_index=1,
            artifact_ref="analysis/report.json",
            content_checksum="abc123",
            deterministic_facts=_valid_analysis_facts(),
        )
        failures = validate_deterministic_artifact_binding(binding)
        assert any("missing job_id" in f for f in failures)

    def test_unknown_phase_fails(self) -> None:
        binding = DeterministicArtifactBinding(
            artifact_role="deterministic",
            artifact_phase="unknown_phase",
            job_id="job-001",
            stage_index=1,
            artifact_ref="analysis/report.json",
            content_checksum="abc123",
            deterministic_facts=_valid_analysis_facts(),
        )
        failures = validate_deterministic_artifact_binding(binding)
        assert any("unknown artifact_phase" in f for f in failures)

    def test_invalid_artifact_role_fails(self) -> None:
        binding = DeterministicArtifactBinding(
            artifact_role="primary",
            artifact_phase="analysis",
            job_id="job-001",
            stage_index=1,
            artifact_ref="analysis/report.json",
            content_checksum="abc123",
            deterministic_facts=_valid_analysis_facts(),
        )
        failures = validate_deterministic_artifact_binding(binding)
        assert any("invalid artifact_role" in f for f in failures)

    def test_stage_index_out_of_range_fails(self) -> None:
        for bad_index in (0, 4, -1):
            binding = DeterministicArtifactBinding(
                artifact_role="deterministic",
                artifact_phase="analysis",
                job_id="job-001",
                stage_index=bad_index,
                artifact_ref="analysis/report.json",
                content_checksum="abc123",
                deterministic_facts=_valid_analysis_facts(),
            )
            failures = validate_deterministic_artifact_binding(binding)
            assert any("stage_index" in f for f in failures)

    def test_missing_deterministic_facts_fails(self) -> None:
        binding = DeterministicArtifactBinding(
            artifact_role="deterministic",
            artifact_phase="analysis",
            job_id="job-001",
            stage_index=1,
            artifact_ref="analysis/report.json",
            content_checksum="abc123",
            deterministic_facts=None,
        )
        failures = validate_deterministic_artifact_binding(binding)
        assert any("missing deterministic_facts" in f for f in failures)

    def test_analysis_facts_require_framework_or_language_or_build_tool(self) -> None:
        facts = DeterministicAnalysisFacts()
        binding = DeterministicArtifactBinding(
            artifact_role="deterministic",
            artifact_phase="analysis",
            job_id="job-001",
            stage_index=1,
            artifact_ref="analysis/report.json",
            content_checksum="abc123",
            deterministic_facts=facts,
        )
        failures = validate_deterministic_artifact_binding(binding)
        assert any("must include at least one" in f for f in failures)

    def test_planning_facts_require_selected_migration_stages(self) -> None:
        facts = DeterministicPlanningFacts()
        binding = DeterministicArtifactBinding(
            artifact_role="deterministic",
            artifact_phase="planning",
            job_id="job-001",
            stage_index=2,
            artifact_ref="planning/plan.yaml",
            content_checksum="abc123",
            deterministic_facts=facts,
        )
        failures = validate_deterministic_artifact_binding(binding)
        assert any("must include selected_migration_stages" in f for f in failures)

    def test_analysis_facts_fields_are_accessible(self) -> None:
        facts = _valid_analysis_facts()
        assert facts.detected_framework == "Spring Boot"
        assert facts.detected_language == "Java"
        assert facts.build_tool == "maven"
        assert facts.source_java_version == "8"
        assert facts.source_spring_boot_version == "2.1.6"
        assert facts.javax_import_count == 45
        assert facts.has_datasource_config is True

    def test_planning_facts_fields_are_accessible(self) -> None:
        facts = _valid_planning_facts()
        assert facts.target_java_version == "17"
        assert facts.target_spring_boot_version == "3.5.14"
        assert "baseline" in facts.selected_migration_stages
        assert facts.executable is True
        assert facts.unit_count == 3


# ── AMF-255: Primary LLM Role ──────────────────────────────────────────


class TestPrimaryLLMInput:
    """Tests for primary LLM input contract."""

    def test_valid_input_passes_validation(self) -> None:
        input_ = _valid_primary_input()
        failures = validate_primary_llm_input(input_)
        assert failures == []

    def test_missing_deterministic_artifact_ref_fails(self) -> None:
        input_ = PrimaryLLMInput(
            deterministic_artifact_ref="",
            deterministic_artifact_checksum="abc123",
            phase="analysis",
            job_id="job-001",
            stage_index=1,
        )
        failures = validate_primary_llm_input(input_)
        assert any("missing deterministic_artifact_ref" in f for f in failures)

    def test_missing_deterministic_artifact_checksum_fails(self) -> None:
        input_ = PrimaryLLMInput(
            deterministic_artifact_ref="analysis/report.json",
            deterministic_artifact_checksum="",
            phase="analysis",
            job_id="job-001",
            stage_index=1,
        )
        failures = validate_primary_llm_input(input_)
        assert any("missing deterministic_artifact_checksum" in f for f in failures)

    def test_unknown_phase_fails(self) -> None:
        input_ = PrimaryLLMInput(
            deterministic_artifact_ref="analysis/report.json",
            deterministic_artifact_checksum="abc123",
            phase="unknown_phase",
            job_id="job-001",
            stage_index=1,
        )
        failures = validate_primary_llm_input(input_)
        assert any("unknown phase" in f for f in failures)

    def test_input_includes_source_and_target_profiles(self) -> None:
        input_ = _valid_primary_input()
        assert input_.source_profile == {"java": "8", "spring_boot": "2.1.6"}
        assert input_.target_profile == {"java": "17", "spring_boot": "3.5.14"}


class TestPrimaryLLMOutput:
    """Tests for primary LLM output contract."""

    def test_valid_output_passes_validation(self) -> None:
        output = _valid_primary_output()
        failures = validate_primary_llm_output(output)
        assert failures == []

    def test_missing_reasoning_fails(self) -> None:
        output = PrimaryLLMOutput(
            reasoning="",
            risks=(),
            confidence=0.5,
            recommended_next_step="proceed",
            draft_markdown="# Summary",
        )
        failures = validate_primary_llm_output(output)
        assert any("missing reasoning" in f for f in failures)

    def test_missing_draft_markdown_fails(self) -> None:
        output = PrimaryLLMOutput(
            reasoning="valid reasoning",
            risks=(),
            confidence=0.5,
            recommended_next_step="proceed",
            draft_markdown="",
        )
        failures = validate_primary_llm_output(output)
        assert any("draft_markdown" in f for f in failures)

    def test_missing_confidence_fails(self) -> None:
        output = PrimaryLLMOutput(
            reasoning="valid reasoning",
            risks=(),
            confidence=-1.0,
            recommended_next_step="proceed",
            draft_markdown="# Summary",
        )
        failures = validate_primary_llm_output(output)
        assert any("confidence" in f for f in failures)

    def test_confidence_out_of_range_fails(self) -> None:
        for bad_conf in (-0.1, 1.1, 99.0):
            output = PrimaryLLMOutput(
                reasoning="valid reasoning",
                risks=("risk1",),
                confidence=bad_conf,
                recommended_next_step="proceed",
                draft_markdown="# Summary",
            )
            failures = validate_primary_llm_output(output)
            assert any("confidence" in f for f in failures)

    def test_execution_instruction_fails(self) -> None:
        output = PrimaryLLMOutput(
            reasoning="You should execute command to update files.",
            risks=(),
            confidence=0.7,
            recommended_next_step="proceed",
            draft_markdown="# Summary",
        )
        failures = validate_primary_llm_output(output)
        assert any("execution instruction" in f for f in failures)

    def test_apply_patch_instruction_fails(self) -> None:
        output = PrimaryLLMOutput(
            reasoning="Valid reasoning.",
            risks=(),
            confidence=0.7,
            recommended_next_step="apply patch to pom.xml",
            draft_markdown="# Summary",
        )
        failures = validate_primary_llm_output(output)
        assert any("execution instruction" in f for f in failures)

    def test_no_execution_instruction_in_normal_text_passes(self) -> None:
        output = PrimaryLLMOutput(
            reasoning="The analysis found javax imports that need migration.",
            risks=("javax-to-jakarta",),
            confidence=0.8,
            recommended_next_step="Proceed to Jakarta migration stage.",
            draft_markdown="# Analysis Summary\n\nThe project needs updates.",
        )
        failures = validate_primary_llm_output(output)
        assert failures == []

    def test_output_computes_checksum(self) -> None:
        output = _valid_primary_output()
        cs = compute_primary_output_checksum(output)
        assert cs
        assert len(cs) == 64  # SHA-256 hex

    def test_computed_checksum_is_deterministic(self) -> None:
        o1 = _valid_primary_output()
        o2 = _valid_primary_output()
        cs1 = compute_primary_output_checksum(o1)
        cs2 = compute_primary_output_checksum(o2)
        assert cs1 == cs2


# ── AMF-256: Reviewer LLM Role ─────────────────────────────────────────


class TestReviewerLLMInput:
    """Tests for reviewer LLM input contract."""

    def test_valid_input_passes_validation(self) -> None:
        input_ = _valid_reviewer_input()
        failures = validate_reviewer_llm_input(input_)
        assert failures == []

    def test_missing_deterministic_artifact_ref_fails(self) -> None:
        input_ = ReviewerLLMInput(
            deterministic_artifact_ref="",
            deterministic_artifact_checksum="abc123",
            primary_output_ref="ref",
            primary_output_checksum="abc",
            primary_reasoning="reasoning",
            draft_markdown="# Summary",
            phase="analysis",
            job_id="job-001",
            stage_index=1,
        )
        failures = validate_reviewer_llm_input(input_)
        assert any("missing deterministic_artifact_ref" in f for f in failures)

    def test_missing_primary_output_ref_fails(self) -> None:
        input_ = ReviewerLLMInput(
            deterministic_artifact_ref="analysis/report.json",
            deterministic_artifact_checksum="abc123",
            primary_output_ref="",
            primary_output_checksum="abc",
            primary_reasoning="reasoning",
            draft_markdown="# Summary",
            phase="analysis",
            job_id="job-001",
            stage_index=1,
        )
        failures = validate_reviewer_llm_input(input_)
        assert any("missing primary_output_ref" in f for f in failures)

    def test_missing_primary_reasoning_fails(self) -> None:
        input_ = ReviewerLLMInput(
            deterministic_artifact_ref="analysis/report.json",
            deterministic_artifact_checksum="abc123",
            primary_output_ref="ref",
            primary_output_checksum="abc",
            primary_reasoning="",
            draft_markdown="# Summary",
            phase="analysis",
            job_id="job-001",
            stage_index=1,
        )
        failures = validate_reviewer_llm_input(input_)
        assert any("missing primary_reasoning" in f for f in failures)

    def test_missing_draft_markdown_fails(self) -> None:
        input_ = ReviewerLLMInput(
            deterministic_artifact_ref="analysis/report.json",
            deterministic_artifact_checksum="abc123",
            primary_output_ref="ref",
            primary_output_checksum="abc",
            primary_reasoning="reasoning",
            draft_markdown="",
            phase="analysis",
            job_id="job-001",
            stage_index=1,
        )
        failures = validate_reviewer_llm_input(input_)
        assert any("missing draft_markdown" in f for f in failures)


class TestReviewerLLMOutput:
    """Tests for reviewer LLM output contract."""

    def test_accept_decision_passes_validation(self) -> None:
        det_cs = sha256_canonical_json({"version": "1.0.0"})
        pri_cs = sha256_canonical_json({"output": "v1"})
        output = ReviewerLLMOutput(
            decision="accept",
            notes=("looks good",),
            confidence=0.9,
            risks=(),
            policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        failures = validate_reviewer_llm_output(output)
        assert failures == []

    def test_invalid_decision_fails(self) -> None:
        output = ReviewerLLMOutput(
            decision="approved",
            notes=(),
            confidence=0.5,
            risks=(),
            policy_concerns=(),
            reviewed_artifact_checksum="abc",
            reviewed_primary_output_checksum="def",
        )
        failures = validate_reviewer_llm_output(output)
        assert any("invalid decision" in f for f in failures)

    def test_confidence_out_of_range_fails(self) -> None:
        for bad_conf in (-0.1, 1.1, 2.0):
            output = ReviewerLLMOutput(
                decision="accept",
                notes=(),
                confidence=bad_conf,
                risks=(),
                policy_concerns=(),
                reviewed_artifact_checksum="abc",
                reviewed_primary_output_checksum="def",
            )
            failures = validate_reviewer_llm_output(output)
            assert any("confidence" in f for f in failures)

    def test_missing_reviewed_artifact_checksum_fails(self) -> None:
        output = ReviewerLLMOutput(
            decision="accept",
            notes=(),
            confidence=0.5,
            risks=(),
            policy_concerns=(),
            reviewed_artifact_checksum="",
            reviewed_primary_output_checksum="def",
        )
        failures = validate_reviewer_llm_output(output)
        assert any("missing reviewed_artifact_checksum" in f for f in failures)

    def test_missing_reviewed_primary_output_checksum_fails(self) -> None:
        output = ReviewerLLMOutput(
            decision="accept",
            notes=(),
            confidence=0.5,
            risks=(),
            policy_concerns=(),
            reviewed_artifact_checksum="abc",
            reviewed_primary_output_checksum="",
        )
        failures = validate_reviewer_llm_output(output)
        assert any("missing reviewed_primary_output_checksum" in f for f in failures)

    def test_output_computes_checksum(self) -> None:
        det_cs = sha256_canonical_json({"version": "1.0.0"})
        pri_cs = sha256_canonical_json({"output": "v1"})
        output = ReviewerLLMOutput(
            decision="accept",
            notes=("good",),
            confidence=0.9,
            risks=(),
            policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        cs = compute_reviewer_output_checksum(output)
        assert cs
        assert len(cs) == 64

    def test_computed_checksum_is_deterministic(self) -> None:
        det_cs = sha256_canonical_json({"version": "1.0.0"})
        pri_cs = sha256_canonical_json({"output": "v1"})
        o1 = ReviewerLLMOutput(
            decision="accept", notes=("good",), confidence=0.9, risks=(),
            policy_concerns=(), reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        o2 = ReviewerLLMOutput(
            decision="accept", notes=("good",), confidence=0.9, risks=(),
            policy_concerns=(), reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        assert compute_reviewer_output_checksum(o1) == compute_reviewer_output_checksum(o2)


# ── Integrated Chain Tests ──────────────────────────────────────────────


class TestReviewerChecksumBinding:
    """Tests for reviewer checksum binding."""

    def test_accept_with_matching_checksums_passes(self) -> None:
        det_cs = "det-abc123"
        pri_cs = "pri-def456"
        output = ReviewerLLMOutput(
            decision="accept",
            notes=(),
            confidence=0.9,
            risks=(),
            policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        failures = validate_checksum_binding(det_cs, pri_cs, output)
        assert failures == []

    def test_checksum_mismatch_on_deterministic_artifact_fails(self) -> None:
        output = ReviewerLLMOutput(
            decision="accept",
            notes=(),
            confidence=0.9,
            risks=(),
            policy_concerns=(),
            reviewed_artifact_checksum="wrong-checksum",
            reviewed_primary_output_checksum="pri-def456",
        )
        failures = validate_checksum_binding("det-abc123", "pri-def456", output)
        assert any("checksum mismatch on deterministic artifact" in f for f in failures)

    def test_checksum_mismatch_on_primary_output_fails(self) -> None:
        output = ReviewerLLMOutput(
            decision="accept",
            notes=(),
            confidence=0.9,
            risks=(),
            policy_concerns=(),
            reviewed_artifact_checksum="det-abc123",
            reviewed_primary_output_checksum="wrong-checksum",
        )
        failures = validate_checksum_binding("det-abc123", "pri-def456", output)
        assert any("checksum mismatch on primary output" in f for f in failures)


class TestReviewedOutputContract:
    """Tests for the full reviewed output contract (integrates all three)."""

    def test_full_accept_chain_passes(self) -> None:
        det_cs = sha256_canonical_json({"version": "1.0.0"})
        pri_cs = sha256_canonical_json({"output": "v1"})
        reviewer_output = ReviewerLLMOutput(
            decision="accept",
            notes=("Evidence matches.",),
            confidence=0.95,
            risks=(),
            policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        result = validate_reviewed_output_contract(det_cs, pri_cs, reviewer_output)
        assert result.ok is True
        assert result.checksum_matched is True
        assert result.decision == "accept"

    def test_reviewer_reject_fails_closed(self) -> None:
        det_cs = sha256_canonical_json({"version": "1.0.0"})
        pri_cs = sha256_canonical_json({"output": "v1"})
        reviewer_output = ReviewerLLMOutput(
            decision="reject",
            notes=("Evidence does not match facts.",),
            confidence=0.3,
            risks=("mismatched evidence",),
            policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        result = validate_reviewed_output_contract(det_cs, pri_cs, reviewer_output)
        assert result.ok is False
        assert any("reviewer rejected" in f for f in result.failures)

    def test_reviewer_request_revision_fails_closed(self) -> None:
        det_cs = sha256_canonical_json({"version": "1.0.0"})
        pri_cs = sha256_canonical_json({"output": "v1"})
        reviewer_output = ReviewerLLMOutput(
            decision="request_revision",
            notes=("Needs more detail on risks.",),
            confidence=0.6,
            risks=("incomplete risk assessment",),
            policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        result = validate_reviewed_output_contract(det_cs, pri_cs, reviewer_output)
        assert result.ok is False
        assert any("reviewer requested revision" in f for f in result.failures)

    def test_checksum_mismatch_fails_closed_even_with_accept(self) -> None:
        det_cs = sha256_canonical_json({"version": "1.0.0"})
        wrong_pri_cs = "wrong-checksum"
        pri_cs = "correct-primary-checksum"
        reviewer_output = ReviewerLLMOutput(
            decision="accept",
            notes=(),
            confidence=0.9,
            risks=(),
            policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=wrong_pri_cs,
        )
        result = validate_reviewed_output_contract(det_cs, pri_cs, reviewer_output)
        assert result.ok is False
        assert result.checksum_matched is False

    def test_malformed_reviewer_output_fails_closed(self) -> None:
        det_cs = sha256_canonical_json({"version": "1.0.0"})
        pri_cs = sha256_canonical_json({"output": "v1"})
        reviewer_output = ReviewerLLMOutput(
            decision="invalid_decision",
            notes=(),
            confidence=0.5,
            risks=(),
            policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        result = validate_reviewed_output_contract(det_cs, pri_cs, reviewer_output)
        assert result.ok is False
        assert any("invalid decision" in f for f in result.failures)


# ── Enums and Dimensions ────────────────────────────────────────────────


class TestReviewChainEnums:
    """Tests for enum values used across the review chain."""

    def test_artifact_phase_values(self) -> None:
        assert ArtifactPhase.ANALYSIS.value == "analysis"
        assert ArtifactPhase.PLANNING.value == "planning"

    def test_reviewer_decision_values(self) -> None:
        assert ReviewerDecision.ACCEPT.value == "accept"
        assert ReviewerDecision.REJECT.value == "reject"
        assert ReviewerDecision.REQUEST_REVISION.value == "request_revision"
        assert ReviewerDecision.MALFORMED.value == "malformed"
        assert ReviewerDecision.STALE.value == "stale"
        assert ReviewerDecision.CHECKSUM_MISMATCH.value == "checksum_mismatch"
        assert ReviewerDecision.REVIEWER_FAILED.value == "reviewer_failed"
        assert ReviewerDecision.FAILED_CLOSED.value == "failed_closed"

    def test_review_dimension_values(self) -> None:
        assert ReviewDimension.EVIDENCE_FIT.value == "evidence_fit"
        assert ReviewDimension.CORRECTNESS.value == "correctness"
        assert ReviewDimension.COMPLETENESS.value == "completeness"
        assert ReviewDimension.CHECKSUM_MATCH.value == "checksum_match"
        assert ReviewDimension.STALE_INPUT_CHECK.value == "stale_input_check"


# ── Immutability Tests ──────────────────────────────────────────────────


class TestContractImmutability:
    """Verify that contract dataclasses are frozen (immutable)."""

    def test_deterministic_artifact_binding_is_frozen(self) -> None:
        binding = _valid_analysis_binding()
        with pytest.raises(Exception):
            binding.artifact_ref = "new-ref"  # type: ignore[misc]

    def test_primary_llm_input_is_frozen(self) -> None:
        input_ = _valid_primary_input()
        with pytest.raises(Exception):
            input_.phase = "planning"  # type: ignore[misc]

    def test_primary_llm_output_is_frozen(self) -> None:
        output = _valid_primary_output()
        with pytest.raises(Exception):
            output.confidence = 1.0  # type: ignore[misc]

    def test_reviewer_llm_input_is_frozen(self) -> None:
        input_ = _valid_reviewer_input()
        with pytest.raises(Exception):
            input_.phase = "planning"  # type: ignore[misc]

    def test_reviewer_llm_output_is_frozen(self) -> None:
        det_cs = sha256_canonical_json({"v": "1"})
        pri_cs = sha256_canonical_json({"o": "1"})
        output = ReviewerLLMOutput(
            decision="accept", notes=(), confidence=0.5, risks=(),
            policy_concerns=(), reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        with pytest.raises(Exception):
            output.decision = "reject"  # type: ignore[misc]


# ── Helpers for extended tests ──────────────────────────────────────────


def _det_cs() -> str:
    return sha256_canonical_json({"version": "1.0.0"})


def _pri_cs() -> str:
    return sha256_canonical_json({"output": "v1"})


def _rev_cs() -> str:
    return sha256_canonical_json({"reviewer": "v1"})


def _valid_metadata() -> FinalMarkdownMetadata:
    return FinalMarkdownMetadata(
        job_id="job-001",
        phase="analysis",
        stage_index=1,
        deterministic_artifact_checksum=_det_cs(),
        primary_output_checksum=_pri_cs(),
        reviewer_output_checksum=_rev_cs(),
        review_decision="accept",
        review_confidence=0.95,
        created_at="2025-01-01T00:00:00.000000Z",
    )


def _valid_final_markdown() -> FinalReviewedMarkdown:
    return FinalReviewedMarkdown(
        summary="Migration analysis complete.",
        inputs_used="analysis/analysis_report.json",
        deterministic_findings="Framework: Spring Boot, Language: Java",
        file_names=("pom.xml", "application.properties"),
        primary_reasoning="The project uses javax.* imports.",
        reviewer_notes="Evidence fits deterministic facts. Accept.",
        risks=("javax-to-jakarta migration complexity",),
        confidence=0.95,
        recommended_next_step="Proceed to Jakarta migration stage.",
        metadata=_valid_metadata(),
    )


# ── AMF-257: Reviewer Decision Matrix ───────────────────────────────────


class TestReviewerDecisionMatrix:
    """Tests for reviewer decision matrix (AMF-257)."""

    def test_accept_with_valid_checksums_passes(self) -> None:
        det_cs = _det_cs()
        pri_cs = _pri_cs()
        output = ReviewerLLMOutput(
            decision="accept",
            notes=("Evidence matches.",),
            confidence=0.95,
            risks=(),
            policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        outcome = resolve_reviewer_decision(output, det_cs, pri_cs)
        assert outcome.ok is True
        assert outcome.blocked is False
        assert outcome.revision_required is False
        assert outcome.checksum_matched is True
        assert outcome.decision == "accept"

    def test_accept_with_checksum_mismatch_fails_closed(self) -> None:
        det_cs = _det_cs()
        wrong_pri = "wrong-checksum"
        output = ReviewerLLMOutput(
            decision="accept",
            notes=("looks good",),
            confidence=0.9,
            risks=(),
            policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=wrong_pri,
        )
        outcome = resolve_reviewer_decision(output, det_cs, _pri_cs())
        assert outcome.ok is False
        assert outcome.blocked is True
        assert outcome.checksum_matched is False
        assert outcome.decision == "checksum_mismatch"

    def test_reject_blocks_acceptance(self) -> None:
        det_cs = _det_cs()
        pri_cs = _pri_cs()
        output = ReviewerLLMOutput(
            decision="reject",
            notes=("Not enough evidence.",),
            confidence=0.3,
            risks=("incomplete analysis",),
            policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        outcome = resolve_reviewer_decision(output, det_cs, pri_cs)
        assert outcome.ok is False
        assert outcome.blocked is True
        assert outcome.revision_required is False
        assert outcome.decision == "reject"

    def test_request_revision_blocks_acceptance(self) -> None:
        det_cs = _det_cs()
        pri_cs = _pri_cs()
        output = ReviewerLLMOutput(
            decision="request_revision",
            notes=("Add more detail on risks.",),
            confidence=0.6,
            risks=("incomplete risk assessment",),
            policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        outcome = resolve_reviewer_decision(output, det_cs, pri_cs)
        assert outcome.ok is False
        assert outcome.blocked is True
        assert outcome.revision_required is True
        assert outcome.decision == "request_revision"

    def test_malformed_reviewer_output_fails_closed(self) -> None:
        det_cs = _det_cs()
        pri_cs = _pri_cs()
        output = ReviewerLLMOutput(
            decision="invalid_decision",
            notes=(),
            confidence=0.5,
            risks=(),
            policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        outcome = resolve_reviewer_decision(output, det_cs, pri_cs)
        assert outcome.ok is False
        assert outcome.blocked is True
        assert outcome.decision == "malformed"

    def test_none_reviewer_output_fails_closed(self) -> None:
        outcome = resolve_reviewer_decision(None, _det_cs(), _pri_cs())
        assert outcome.ok is False
        assert outcome.blocked is True
        assert outcome.decision == "failed_closed"

    def test_stale_decision_fails_closed(self) -> None:
        outcome = resolve_stale_decision()
        assert outcome.ok is False
        assert outcome.blocked is True
        assert outcome.decision == "stale"

    def test_reviewer_failed_decision_fails_closed(self) -> None:
        outcome = resolve_reviewer_failed_decision()
        assert outcome.ok is False
        assert outcome.blocked is True
        assert outcome.decision == "reviewer_failed"

    def test_failed_closed_with_custom_reason(self) -> None:
        outcome = resolve_failed_closed_decision("timeout after 30s")
        assert outcome.ok is False
        assert outcome.blocked is True
        assert outcome.decision == "failed_closed"
        assert "timeout after 30s" in outcome.reason

    def test_is_decision_failed_closed_all_states(self) -> None:
        det_cs = _det_cs()
        pri_cs = _pri_cs()
        assert is_decision_failed_closed(
            ReviewerDecisionOutcome(decision="failed_closed", ok=False, blocked=True,
                                    revision_required=False, reason="x", checksum_matched=False)
        )
        assert is_decision_failed_closed(
            ReviewerDecisionOutcome(decision="malformed", ok=False, blocked=True,
                                    revision_required=False, reason="x", checksum_matched=False)
        )
        assert is_decision_failed_closed(
            ReviewerDecisionOutcome(decision="stale", ok=False, blocked=True,
                                    revision_required=False, reason="x", checksum_matched=False)
        )
        assert is_decision_failed_closed(
            ReviewerDecisionOutcome(decision="checksum_mismatch", ok=False, blocked=True,
                                    revision_required=False, reason="x", checksum_matched=False)
        )
        assert is_decision_failed_closed(
            ReviewerDecisionOutcome(decision="reviewer_failed", ok=False, blocked=True,
                                    revision_required=False, reason="x", checksum_matched=False)
        )
        output = ReviewerLLMOutput(
            decision="accept", notes=("ok",), confidence=0.9, risks=(),
            policy_concerns=(), reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        ok_outcome = resolve_reviewer_decision(output, det_cs, pri_cs)
        assert is_decision_failed_closed(ok_outcome) is False

    def test_can_produce_final_artifact_only_for_accepted(self) -> None:
        assert can_produce_final_artifact(
            ReviewerDecisionOutcome(decision="accept", ok=True, blocked=False,
                                    revision_required=False, reason="", checksum_matched=True)
        )
        assert not can_produce_final_artifact(
            ReviewerDecisionOutcome(decision="reject", ok=False, blocked=True,
                                    revision_required=False, reason="", checksum_matched=True)
        )
        assert not can_produce_final_artifact(
            ReviewerDecisionOutcome(decision="failed_closed", ok=False, blocked=True,
                                    revision_required=False, reason="", checksum_matched=False)
        )


# ── AMF-258: Final Reviewed Markdown Artifact Schema ────────────────────


class TestFinalReviewedMarkdown:
    """Tests for final reviewed Markdown artifact schema (AMF-258)."""

    def test_valid_final_markdown_passes_validation(self) -> None:
        artifact = _valid_final_markdown()
        failures = validate_final_markdown(artifact)
        assert failures == []

    def test_final_markdown_requires_all_sections(self) -> None:
        artifact = FinalReviewedMarkdown(
            summary="",
            inputs_used="",
            deterministic_findings="",
            file_names=(),
            primary_reasoning="",
            reviewer_notes="",
            risks=(),
            confidence=0.0,
            recommended_next_step="",
            metadata=_valid_metadata(),
        )
        failures = validate_final_markdown(artifact)
        required = {"summary", "inputs_used", "deterministic_findings",
                     "file_names", "primary_reasoning", "reviewer_notes",
                     "risks", "recommended_next_step"}
        missing = {f for f in failures if f.startswith("missing required section")}
        assert len(missing) >= len(required)

    def test_final_markdown_requires_deterministic_checksum(self) -> None:
        meta = _valid_metadata()
        meta = FinalMarkdownMetadata(
            job_id="job-001", phase="analysis", stage_index=1,
            primary_output_checksum=_pri_cs(), reviewer_output_checksum=_rev_cs(),
            review_decision="accept", deterministic_artifact_checksum="",
        )
        artifact = FinalReviewedMarkdown(
            summary="S", inputs_used="I", deterministic_findings="D", file_names=("f",),
            primary_reasoning="P", reviewer_notes="R", risks=("r",), confidence=0.5,
            recommended_next_step="N", metadata=meta,
        )
        failures = validate_final_markdown(artifact)
        assert any("missing deterministic_artifact_checksum" in f for f in failures)

    def test_final_markdown_requires_primary_output_checksum(self) -> None:
        meta = _valid_metadata()
        meta = FinalMarkdownMetadata(
            job_id="job-001", phase="analysis", stage_index=1,
            deterministic_artifact_checksum=_det_cs(), reviewer_output_checksum=_rev_cs(),
            review_decision="accept", primary_output_checksum="",
        )
        artifact = FinalReviewedMarkdown(
            summary="S", inputs_used="I", deterministic_findings="D", file_names=("f",),
            primary_reasoning="P", reviewer_notes="R", risks=("r",), confidence=0.5,
            recommended_next_step="N", metadata=meta,
        )
        failures = validate_final_markdown(artifact)
        assert any("missing primary_output_checksum" in f for f in failures)

    def test_final_markdown_requires_reviewer_output_checksum(self) -> None:
        meta = _valid_metadata()
        meta = FinalMarkdownMetadata(
            job_id="job-001", phase="analysis", stage_index=1,
            deterministic_artifact_checksum=_det_cs(), primary_output_checksum=_pri_cs(),
            review_decision="accept", reviewer_output_checksum="",
        )
        artifact = FinalReviewedMarkdown(
            summary="S", inputs_used="I", deterministic_findings="D", file_names=("f",),
            primary_reasoning="P", reviewer_notes="R", risks=("r",), confidence=0.5,
            recommended_next_step="N", metadata=meta,
        )
        failures = validate_final_markdown(artifact)
        assert any("missing reviewer_output_checksum" in f for f in failures)

    def test_final_markdown_rejects_unaccepted_reviewer(self) -> None:
        meta = _valid_metadata()
        meta = FinalMarkdownMetadata(
            job_id="job-001", phase="analysis", stage_index=1,
            deterministic_artifact_checksum=_det_cs(), primary_output_checksum=_pri_cs(),
            reviewer_output_checksum=_rev_cs(), review_decision="reject",
        )
        artifact = FinalReviewedMarkdown(
            summary="S", inputs_used="I", deterministic_findings="D", file_names=("f",),
            primary_reasoning="P", reviewer_notes="R", risks=("r",), confidence=0.5,
            recommended_next_step="N", metadata=meta,
        )
        failures = validate_final_markdown(artifact)
        assert any("requires accepted reviewer decision" in f for f in failures)

    def test_final_markdown_exposes_safe_refs_only(self) -> None:
        artifact = _valid_final_markdown()
        assert hasattr(artifact, "safe_artifact_refs")
        assert "sandbox" not in str(artifact.safe_artifact_refs).lower()

    def test_final_markdown_rejects_forbidden_file_names(self) -> None:
        meta = _valid_metadata()
        artifact = FinalReviewedMarkdown(
            summary="S", inputs_used="I", deterministic_findings="D",
            file_names=("sandbox/evil.txt",),
            primary_reasoning="P", reviewer_notes="R", risks=("r",), confidence=0.5,
            recommended_next_step="N", metadata=meta,
        )
        failures = validate_final_markdown(artifact)
        assert any("forbidden path" in f for f in failures)

    def test_final_markdown_checksum_is_deterministic(self) -> None:
        a1 = _valid_final_markdown()
        a2 = _valid_final_markdown()
        assert compute_final_markdown_checksum(a1) == compute_final_markdown_checksum(a2)

    def test_final_markdown_rejects_invalid_confidence(self) -> None:
        meta = _valid_metadata()
        artifact = FinalReviewedMarkdown(
            summary="S", inputs_used="I", deterministic_findings="D", file_names=("f",),
            primary_reasoning="P", reviewer_notes="R", risks=("r",), confidence=2.0,
            recommended_next_step="N", metadata=meta,
        )
        failures = validate_final_markdown(artifact)
        assert any("confidence" in f for f in failures)


# ── AMF-260: Metadata and Checksum Binding ──────────────────────────────


class TestMetadataAndChecksumBinding:
    """Tests for metadata and checksum binding (AMF-260)."""

    def _valid_chain(self) -> CompleteChecksumChain:
        return CompleteChecksumChain(
            deterministic_artifact_checksum=_det_cs(),
            primary_input_checksum=sha256_canonical_json({"input": "v1"}),
            primary_output_checksum=_pri_cs(),
            reviewer_input_checksum=sha256_canonical_json({"rev_input": "v1"}),
            reviewer_output_checksum=_rev_cs(),
            final_markdown_checksum=sha256_canonical_json({"final": "v1"}),
            job_id="job-001",
            phase="analysis",
            stage_index=1,
            review_decision="accept",
            review_confidence=0.95,
        )

    def test_complete_checksum_chain_validates(self) -> None:
        chain = self._valid_chain()
        failures = validate_complete_checksum_chain(chain)
        assert failures == []

    def test_missing_deterministic_checksum_fails(self) -> None:
        chain = self._valid_chain()
        chain = CompleteChecksumChain(
            deterministic_artifact_checksum="",
            primary_input_checksum=chain.primary_input_checksum,
            primary_output_checksum=chain.primary_output_checksum,
            reviewer_input_checksum=chain.reviewer_input_checksum,
            reviewer_output_checksum=chain.reviewer_output_checksum,
            final_markdown_checksum=chain.final_markdown_checksum,
            job_id=chain.job_id, phase=chain.phase, stage_index=chain.stage_index,
        )
        failures = validate_complete_checksum_chain(chain)
        assert any("missing deterministic_artifact_checksum" in f for f in failures)

    def test_missing_primary_output_checksum_fails(self) -> None:
        chain = self._valid_chain()
        chain = CompleteChecksumChain(
            deterministic_artifact_checksum=chain.deterministic_artifact_checksum,
            primary_input_checksum=chain.primary_input_checksum,
            primary_output_checksum="",
            reviewer_input_checksum=chain.reviewer_input_checksum,
            reviewer_output_checksum=chain.reviewer_output_checksum,
            final_markdown_checksum=chain.final_markdown_checksum,
            job_id=chain.job_id, phase=chain.phase, stage_index=chain.stage_index,
        )
        failures = validate_complete_checksum_chain(chain)
        assert any("missing primary_output_checksum" in f for f in failures)

    def test_missing_reviewer_output_checksum_fails(self) -> None:
        chain = self._valid_chain()
        chain = CompleteChecksumChain(
            deterministic_artifact_checksum=chain.deterministic_artifact_checksum,
            primary_input_checksum=chain.primary_input_checksum,
            primary_output_checksum=chain.primary_output_checksum,
            reviewer_input_checksum=chain.reviewer_input_checksum,
            reviewer_output_checksum="",
            final_markdown_checksum=chain.final_markdown_checksum,
            job_id=chain.job_id, phase=chain.phase, stage_index=chain.stage_index,
        )
        failures = validate_complete_checksum_chain(chain)
        assert any("missing reviewer_output_checksum" in f for f in failures)

    def test_missing_final_markdown_checksum_fails(self) -> None:
        chain = self._valid_chain()
        chain = CompleteChecksumChain(
            deterministic_artifact_checksum=chain.deterministic_artifact_checksum,
            primary_input_checksum=chain.primary_input_checksum,
            primary_output_checksum=chain.primary_output_checksum,
            reviewer_input_checksum=chain.reviewer_input_checksum,
            reviewer_output_checksum=chain.reviewer_output_checksum,
            final_markdown_checksum="",
            job_id=chain.job_id, phase=chain.phase, stage_index=chain.stage_index,
        )
        failures = validate_complete_checksum_chain(chain)
        assert any("missing final_markdown_checksum" in f for f in failures)

    def test_foreign_job_id_fails(self) -> None:
        chain = self._valid_chain()
        failures = validate_checksum_chain_against_reference(
            chain, "other-job-999", chain.phase, chain.stage_index,
        )
        assert any("foreign job_id" in f for f in failures)

    def test_wrong_phase_fails(self) -> None:
        chain = self._valid_chain()
        failures = validate_checksum_chain_against_reference(
            chain, chain.job_id, "planning", chain.stage_index,
        )
        assert any("wrong phase" in f for f in failures)

    def test_wrong_stage_index_fails(self) -> None:
        chain = self._valid_chain()
        failures = validate_checksum_chain_against_reference(
            chain, chain.job_id, chain.phase, 3,
        )
        assert any("wrong stage_index" in f for f in failures)

    def test_stale_checksum_detected(self) -> None:
        assert is_checksum_stale("abc", "def") is True
        assert is_checksum_stale("abc", "abc") is False
        assert is_checksum_stale("", "abc") is False
        assert is_checksum_stale("abc", "") is False

    def test_metadata_is_safe_no_forbidden_fields(self) -> None:
        chain = self._valid_chain()
        metadata = safe_metadata_dict(chain)
        forbidden = {"provider", "endpoint", "deployment", "sandbox_path",
                     "argv", "env", "env_ref", "raw_command"}
        for key in metadata:
            assert key not in forbidden, f"forbidden key {key} in metadata"

    def test_metadata_validation_rejects_forbidden_fields(self) -> None:
        failures = validate_metadata_safety({"provider": "openai", "model": "gpt-4"})
        assert any("forbidden field" in f for f in failures)

    def test_metadata_validation_passes_safe_fields(self) -> None:
        failures = validate_metadata_safety({
            "job_id": "j1", "phase": "analysis", "stage_index": 1,
        })
        assert failures == []

    def test_metadata_validation_rejects_nested_dict_forbidden(self) -> None:
        failures = validate_metadata_safety({
            "phase": "analysis",
            "inner": {"sandbox_path": "/tmp/secrets"},
        })
        assert any("forbidden field" in f for f in failures)
        assert any("sandbox_path" in f for f in failures)

    def test_metadata_validation_rejects_list_of_dicts_forbidden(self) -> None:
        failures = validate_metadata_safety({
            "phase": "analysis",
            "items": [
                {"name": "safe"},
                {"argv": ["--leak", "secrets"]},
            ],
        })
        assert any("forbidden field" in f for f in failures)
        assert any("argv" in f for f in failures)

    def test_metadata_validation_rejects_deeply_nested_forbidden(self) -> None:
        failures = validate_metadata_safety({
            "phase": "analysis",
            "level1": {
                "level2": {
                    "level3": {"env": {"SECRET": "value"}},
                },
            },
        })
        assert any("forbidden field" in f for f in failures)
        assert any("env" in f for f in failures)

    def test_metadata_validation_rejects_dataclass_with_forbidden_field(self) -> None:
        meta = FinalMarkdownMetadata(
            job_id="job-001",
            phase="analysis",
            stage_index=1,
            deterministic_artifact_checksum=_det_cs(),
            primary_output_checksum=_pri_cs(),
            reviewer_output_checksum=_rev_cs(),
            review_decision="accept",
            source_profile="prod",
            target_profile="target",
        )
        failures = validate_metadata_safety({"nested": meta})
        assert failures == []

        from dataclasses import dataclass

        @dataclass
        class LeakyMeta:
            env: dict[str, str]

        leaky = LeakyMeta(env={"SECRET": "value"})
        failures = validate_metadata_safety({"nested": leaky})
        assert any("forbidden field" in f for f in failures)
        assert any("env" in f for f in failures)

    def test_metadata_validation_rejects_mixed_dataclass_dict_nesting(self) -> None:
        failures = validate_metadata_safety({
            "phase": "analysis",
            "config": {
                "nested": {"provider": "openai", "endpoint": "https://api"},
            },
        })
        assert len(failures) == 2
        assert any("forbidden field" in f for f in failures)
        assert any("provider" in f for f in failures)
        assert any("endpoint" in f for f in failures)

    def test_chain_rejects_non_accept_review_decision(self) -> None:
        chain = self._valid_chain()
        chain = CompleteChecksumChain(
            deterministic_artifact_checksum=chain.deterministic_artifact_checksum,
            primary_input_checksum=chain.primary_input_checksum,
            primary_output_checksum=chain.primary_output_checksum,
            reviewer_input_checksum=chain.reviewer_input_checksum,
            reviewer_output_checksum=chain.reviewer_output_checksum,
            final_markdown_checksum=chain.final_markdown_checksum,
            job_id=chain.job_id, phase=chain.phase, stage_index=chain.stage_index,
            review_decision="reject",
        )
        failures = validate_complete_checksum_chain(chain)
        assert any("must be 'accept'" in f for f in failures)


# ── AMF-259: Retry and Revision Behavior ────────────────────────────────


class TestRetryAndRevisionBehavior:
    """Tests for retry and revision behavior (AMF-259)."""

    def _valid_revision_request(self, revision_number: int = 1) -> RevisionRequest:
        return RevisionRequest(
            job_id="job-001",
            phase="analysis",
            stage_index=1,
            artifact_ref="analysis/analysis_report.json",
            previous_deterministic_checksum=_det_cs(),
            previous_primary_output_checksum=_pri_cs(),
            previous_reviewer_output_checksum=_rev_cs(),
            reviewer_decision="request_revision",
            reviewer_notes=("Needs more detail.",),
            revision_number=revision_number,
            user_comments=("Add risk breakdown.",),
            revision_reason="Incomplete risk assessment",
        )

    def test_reject_creates_blocked_state(self) -> None:
        outcome = ReviewerDecisionOutcome(
            decision="reject", ok=False, blocked=True,
            revision_required=False, reason="reviewer rejected",
            checksum_matched=True, notes=("not good enough",),
        )
        result = build_artifact_rejection_result(
            job_id="job-001", outcome=outcome,
            deterministic_checksum=_det_cs(),
            primary_output_checksum=_pri_cs(),
            reviewer_output_checksum=_rev_cs(),
        )
        assert result.blocked is True
        assert result.revision_required is False
        assert result.reviewer_decision == "reject"
        assert result.rejection_reason == "reviewer rejected"

    def test_request_revision_creates_revision_required_state(self) -> None:
        outcome = ReviewerDecisionOutcome(
            decision="request_revision", ok=False, blocked=True,
            revision_required=True, reason="needs revision",
            checksum_matched=True, notes=("add more detail",),
        )
        result = build_artifact_rejection_result(
            job_id="job-001", outcome=outcome,
            deterministic_checksum=_det_cs(),
            primary_output_checksum=_pri_cs(),
            reviewer_output_checksum=_rev_cs(),
            user_comments=("Please expand risks.",),
        )
        assert result.blocked is True
        assert result.revision_required is True
        assert result.reviewer_decision == "request_revision"
        assert "Please expand" in result.user_comments[0]
        assert result.reviewer_notes == outcome.notes

    def test_valid_revision_request_passes_validation(self) -> None:
        request = self._valid_revision_request()
        failures = validate_revision_request(request)
        assert failures == []

    def test_revision_request_requires_reject_or_request_revision(self) -> None:
        request = RevisionRequest(
            job_id="job-001", phase="analysis", stage_index=1,
            artifact_ref="ref",
            previous_deterministic_checksum=_det_cs(),
            previous_primary_output_checksum=_pri_cs(),
            previous_reviewer_output_checksum=_rev_cs(),
            reviewer_decision="accept",
            reviewer_notes=(), revision_number=1,
        )
        failures = validate_revision_request(request)
        assert any("requires reject or request_revision" in f for f in failures)

    def test_revision_request_preserves_reviewer_notes(self) -> None:
        request = self._valid_revision_request()
        assert "Needs more detail." in request.reviewer_notes

    def test_revision_request_preserves_user_comments(self) -> None:
        request = self._valid_revision_request()
        assert "Add risk breakdown." in request.user_comments

    def test_stale_revision_input_fails(self) -> None:
        request = RevisionRequest(
            job_id="job-001", phase="analysis", stage_index=1,
            artifact_ref="ref",
            previous_deterministic_checksum="",
            previous_primary_output_checksum=_pri_cs(),
            previous_reviewer_output_checksum=_rev_cs(),
            reviewer_decision="request_revision",
            reviewer_notes=("fix",), revision_number=1,
        )
        failures = validate_revision_request(request)
        assert any("missing previous_deterministic_checksum" in f for f in failures)

    def test_duplicate_revision_is_idempotent(self) -> None:
        r1 = self._valid_revision_request(1)
        r2 = self._valid_revision_request(1)
        assert is_revision_idempotent(r1, r2) is True

    def test_different_revision_not_idempotent(self) -> None:
        r1 = self._valid_revision_request(1)
        r2 = RevisionRequest(
            job_id="job-001", phase="analysis", stage_index=1,
            artifact_ref="ref",
            previous_deterministic_checksum="different-checksum",
            previous_primary_output_checksum=_pri_cs(),
            previous_reviewer_output_checksum=_rev_cs(),
            reviewer_decision="request_revision",
            reviewer_notes=("fix",), revision_number=1,
        )
        assert is_revision_idempotent(r1, r2) is False

    def test_checkpoint_acceptance_remains_blocked_for_reject(self) -> None:
        outcome = ReviewerDecisionOutcome(
            decision="reject", ok=False, blocked=True,
            revision_required=False, reason="rejected",
            checksum_matched=True,
        )
        assert is_checkpoint_acceptance_blocked(outcome) is True

    def test_checkpoint_acceptance_remains_blocked_for_request_revision(self) -> None:
        outcome = ReviewerDecisionOutcome(
            decision="request_revision", ok=False, blocked=True,
            revision_required=True, reason="revision",
            checksum_matched=True,
        )
        assert is_checkpoint_acceptance_blocked(outcome) is True

    def test_checkpoint_acceptance_not_blocked_for_accepted(self) -> None:
        outcome = ReviewerDecisionOutcome(
            decision="accept", ok=True, blocked=False,
            revision_required=False, reason="accepted",
            checksum_matched=True,
        )
        assert is_checkpoint_acceptance_blocked(outcome) is False

    def test_revision_number_exceeds_limit_fails(self) -> None:
        request = self._valid_revision_request(6)
        failures = validate_revision_request(request)
        assert any("exceeds limit" in f for f in failures)

    def test_revision_number_zero_fails(self) -> None:
        request = self._valid_revision_request(0)
        failures = validate_revision_request(request)
        assert any("must be >= 1" in f for f in failures)


# ── AMF-261: Reviewer-Required Test Matrix ──────────────────────────────


class TestReviewerRequiredMatrix:
    """Full reviewer-required test matrix proving reviewer is mandatory (AMF-261)."""

    def test_deterministic_artifact_comes_before_primary_for_analysis(self) -> None:
        binding = _valid_analysis_binding()
        failures = validate_deterministic_artifact_binding(binding)
        assert failures == []
        assert binding.artifact_phase == "analysis"

    def test_deterministic_artifact_comes_before_primary_for_planning(self) -> None:
        binding = _valid_planning_binding()
        failures = validate_deterministic_artifact_binding(binding)
        assert failures == []
        assert binding.artifact_phase == "planning"

    def test_primary_output_alone_is_not_enough(self) -> None:
        primary_output = _valid_primary_output()
        det_cs = _det_cs()
        pri_cs = compute_primary_output_checksum(primary_output)

        reviewer_output = ReviewerLLMOutput(
            decision="reject", notes=("missing reviewer",), confidence=0.0,
            risks=(), policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        outcome = resolve_reviewer_decision(reviewer_output, det_cs, pri_cs)
        assert outcome.ok is False

    def test_reviewer_output_is_required_for_analysis(self) -> None:
        det_cs = _det_cs()
        pri_cs = _pri_cs()
        reviewer_output = _valid_reviewer_output(det_cs, pri_cs)
        result = validate_reviewed_output_contract(det_cs, pri_cs, reviewer_output)
        assert result.ok is True

    def test_reviewer_output_is_required_for_planning(self) -> None:
        det_cs = sha256_canonical_json({"plan": "v2"})
        pri_cs = sha256_canonical_json({"plan_output": "v2"})
        reviewer_output = _valid_reviewer_output(det_cs, pri_cs)
        result = validate_reviewed_output_contract(det_cs, pri_cs, reviewer_output)
        assert result.ok is True

    def test_missing_reviewer_fails_closed(self) -> None:
        outcome = resolve_reviewer_decision(None, _det_cs(), _pri_cs())
        assert outcome.ok is False
        assert outcome.decision == "failed_closed"

    def test_stale_reviewer_fails_closed(self) -> None:
        outcome = resolve_stale_decision()
        assert outcome.ok is False
        assert outcome.decision == "stale"

    def test_rejected_reviewer_fails_closed_for_acceptance(self) -> None:
        det_cs = _det_cs()
        pri_cs = _pri_cs()
        reviewer_output = _valid_reviewer_output(det_cs, pri_cs)
        reviewer_output = ReviewerLLMOutput(
            decision="reject", notes=("invalid",), confidence=0.2,
            risks=("bad evidence",), policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        result = validate_reviewed_output_contract(det_cs, pri_cs, reviewer_output)
        assert result.ok is False
        assert any("reviewer rejected" in f for f in result.failures)

    def test_request_revision_fails_closed_for_acceptance(self) -> None:
        det_cs = _det_cs()
        pri_cs = _pri_cs()
        reviewer_output = ReviewerLLMOutput(
            decision="request_revision", notes=("needs work",), confidence=0.5,
            risks=(), policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        result = validate_reviewed_output_contract(det_cs, pri_cs, reviewer_output)
        assert result.ok is False

    def test_malformed_reviewer_fails_closed(self) -> None:
        det_cs = _det_cs()
        pri_cs = _pri_cs()
        reviewer_output = ReviewerLLMOutput(
            decision="bad", notes=(), confidence=0.5, risks=(),
            policy_concerns=(), reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        result = validate_reviewed_output_contract(det_cs, pri_cs, reviewer_output)
        assert result.ok is False

    def test_checksum_mismatch_fails_closed(self) -> None:
        det_cs = _det_cs()
        wrong_pri = "wrong-checksum"
        reviewer_output = ReviewerLLMOutput(
            decision="accept", notes=(), confidence=0.9, risks=(),
            policy_concerns=(), reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=wrong_pri,
        )
        outcome = resolve_reviewer_decision(reviewer_output, det_cs, _pri_cs())
        assert outcome.ok is False
        assert outcome.checksum_matched is False

    def test_reviewer_failed_fails_closed(self) -> None:
        outcome = resolve_reviewer_failed_decision()
        assert outcome.ok is False

    def test_deterministic_fallback_alone_cannot_satisfy(self) -> None:
        binding = _valid_analysis_binding()
        failures = validate_deterministic_artifact_binding(binding)
        assert failures == []

        reviewer_outcome = resolve_reviewer_decision(None, _det_cs(), _pri_cs())
        assert reviewer_outcome.ok is False
        assert reviewer_outcome.decision == "failed_closed"

        assert not can_produce_final_artifact(reviewer_outcome)

    def test_raw_primary_output_is_not_enough(self) -> None:
        primary_output = _valid_primary_output()
        failures = validate_primary_llm_output(primary_output)
        assert failures == []

        det_cs = _det_cs()
        pri_cs = compute_primary_output_checksum(primary_output)
        reject_output = ReviewerLLMOutput(
            decision="reject", notes=("no reviewer",), confidence=0.0,
            risks=(), policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        outcome = resolve_reviewer_decision(reject_output, det_cs, pri_cs)
        assert outcome.ok is False

        assert can_produce_final_artifact(outcome) is False

    def test_f2_chain_happy_path_full(self) -> None:
        det_cs = _det_cs()
        primary_output = _valid_primary_output()
        pri_cs = compute_primary_output_checksum(primary_output)

        reviewer_output = ReviewerLLMOutput(
            decision="accept",
            notes=("Evidence matches facts.", "Ready for downstream."),
            confidence=0.95,
            risks=("javax-to-jakarta migration risk",),
            policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        reviewer_cs = compute_reviewer_output_checksum(reviewer_output)

        outcome = resolve_reviewer_decision(reviewer_output, det_cs, pri_cs)
        assert outcome.ok is True
        assert outcome.checksum_matched is True
        assert outcome.decision == "accept"

        assert can_produce_final_artifact(outcome) is True

        metadata = FinalMarkdownMetadata(
            job_id="job-001", phase="analysis", stage_index=1,
            deterministic_artifact_checksum=det_cs,
            primary_output_checksum=pri_cs,
            reviewer_output_checksum=reviewer_cs,
            review_decision="accept",
            review_confidence=0.95,
        )
        final_artifact = FinalReviewedMarkdown(
            summary="Analysis summary.",
            inputs_used="analysis/analysis_report.json",
            deterministic_findings="Spring Boot 2.1.6, Java 8, javax.* imports found",
            file_names=("pom.xml", "src/main/java/App.java"),
            primary_reasoning=primary_output.reasoning,
            reviewer_notes="; ".join(reviewer_output.notes),
            risks=primary_output.risks,
            confidence=0.95,
            recommended_next_step=primary_output.recommended_next_step,
            metadata=metadata,
        )
        final_failures = validate_final_markdown(final_artifact)
        assert final_failures == []

        final_cs = compute_final_markdown_checksum(final_artifact)
        assert len(final_cs) == 64

        chain = CompleteChecksumChain(
            deterministic_artifact_checksum=det_cs,
            primary_input_checksum=sha256_canonical_json({"input": "ctx"}),
            primary_output_checksum=pri_cs,
            reviewer_input_checksum=sha256_canonical_json({"rev_input": "ctx"}),
            reviewer_output_checksum=reviewer_cs,
            final_markdown_checksum=final_cs,
            job_id="job-001",
            phase="analysis",
            stage_index=1,
            review_decision="accept",
            review_confidence=0.95,
        )
        chain_failures = validate_complete_checksum_chain(chain)
        assert chain_failures == []

    def test_final_artifact_missing_fails_downstream(self) -> None:
        outcome = resolve_reviewer_decision(None, _det_cs(), _pri_cs())
        assert outcome.ok is False
        assert outcome.decision == "failed_closed"

        assert is_decision_failed_closed(outcome) is True

    def test_downstream_raw_primary_resolution_is_rejected(self) -> None:
        primary_output = _valid_primary_output()
        det_cs = _det_cs()
        pri_cs = compute_primary_output_checksum(primary_output)

        reject = ReviewerLLMOutput(
            decision="request_revision",
            notes=("Cannot proceed without review.",),
            confidence=0.0,
            risks=("no review",),
            policy_concerns=(),
            reviewed_artifact_checksum=det_cs,
            reviewed_primary_output_checksum=pri_cs,
        )
        outcome = resolve_reviewer_decision(reject, det_cs, pri_cs)
        assert outcome.ok is False
        assert outcome.blocked is True
        assert can_produce_final_artifact(outcome) is False
