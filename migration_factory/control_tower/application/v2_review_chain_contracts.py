"""F2 review-chain contracts — deterministic artifact, primary LLM, reviewer LLM,
final reviewed Markdown, metadata/checksum binding, and retry/revision behavior.

Core rule: A model reviews another model.
Deterministic fallback alone must not satisfy a model-required reviewed artifact.

The chain:
  deterministic artifact
  -> primary LLM output
  -> reviewer LLM validation
  -> final reviewed Markdown artifact

AMF-254: Deterministic artifact contract
AMF-255: Primary LLM role
AMF-256: Reviewer LLM role
AMF-257: Reviewer decision matrix
AMF-258: Final reviewed Markdown artifact schema
AMF-259: Retry and revision behavior
AMF-260: Metadata and checksum binding

This module defines the contract shapes and fail-closed validation.
It does NOT implement execution, approval, filesystem authority, or persistence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from migration_factory.control_tower.domain.checksums import sha256_canonical_json


# ── Enums ──────────────────────────────────────────────────────────────


class ArtifactPhase(str, Enum):
    ANALYSIS = "analysis"
    PLANNING = "planning"


class ReviewerDecision(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    REQUEST_REVISION = "request_revision"
    MALFORMED = "malformed"
    STALE = "stale"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    REVIEWER_FAILED = "reviewer_failed"
    FAILED_CLOSED = "failed_closed"


class RevisionState(str, Enum):
    NO_REVISION_NEEDED = "no_revision_needed"
    REVISION_REQUIRED = "revision_required"
    REVISION_IN_PROGRESS = "revision_in_progress"
    REVISION_ACCEPTED = "revision_accepted"
    REVISION_REJECTED = "revision_rejected"


class FinalMarkdownSection(str, Enum):
    SUMMARY = "summary"
    INPUTS_USED = "inputs_used"
    DETERMINISTIC_FINDINGS = "deterministic_findings"
    FILE_NAMES = "file_names"
    PRIMARY_REASONING = "primary_reasoning"
    REVIEWER_NOTES = "reviewer_notes"
    RISKS = "risks"
    CONFIDENCE = "confidence"
    RECOMMENDED_NEXT_STEP = "recommended_next_step"
    METADATA = "metadata"


class ReviewDimension(str, Enum):
    EVIDENCE_FIT = "evidence_fit"
    CORRECTNESS = "correctness"
    COMPLETENESS = "completeness"
    RISK_ASSESSMENT = "risk_assessment"
    POLICY_CONCERNS = "policy_concerns"
    CHECKSUM_MATCH = "checksum_match"
    STALE_INPUT_CHECK = "stale_input_check"


# ── Forbidden field patterns ───────────────────────────────────────────

# Fields that must NEVER appear in primary LLM input/output or reviewer context.
# These leak runtime internals and are forbidden by the three-tier permission model.
_FORBIDDEN_TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    "sandbox_path",
    "argv",
    "env",
    "raw_command",
    "filesystem_target",
    "provider",
    "endpoint",
    "deployment",
    "env_ref",
    "user_supplied_file_path",
})

_FORBIDDEN_DICT_KEY_SUBSTRINGS: tuple[str, ...] = (
    "provider",
    "endpoint",
    "deployment",
    "sandbox",
    "argv",
    "env_ref",
)


# ── AMF-254: Deterministic artifact contract ───────────────────────────


@dataclass(frozen=True)
class DeterministicAnalysisFacts:
    """Required deterministic facts extracted by the Analysis agent.

    These ground all model-required output before primary LLM reasoning.
    """

    detected_framework: str | None = None
    detected_language: str | None = None
    build_tool: str | None = None
    source_java_version: str | None = None
    source_spring_boot_version: str | None = None
    dependency_count: int | None = None
    javax_import_count: int | None = None
    jakarta_import_count: int | None = None
    spring_import_count: int | None = None
    module_count: int | None = None
    test_file_count: int | None = None
    has_datasource_config: bool = False
    has_security_config: bool = False
    has_actuator_config: bool = False
    openrewrite_impact: str | None = None
    openrewrite_risk: str | None = None
    risk_facts: tuple[str, ...] = ()
    uncertainty_notes: tuple[str, ...] = ()
    file_refs_checksums: tuple[tuple[str, str], ...] = ()  # (path, checksum)


@dataclass(frozen=True)
class DeterministicPlanningFacts:
    """Required deterministic facts produced by the Planning agent.

    These define the migration plan before primary LLM reasoning.
    """

    selected_migration_stages: tuple[str, ...] = ()
    included_stages: tuple[str, ...] = ()
    excluded_skipped_stages: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    target_java_version: str | None = None
    target_spring_boot_version: str | None = None
    profile_id: str | None = None
    strategy: str | None = None
    risk_level: str | None = None
    executable: bool = False
    requires_human_approval: bool = True
    blocker_count: int = 0
    warning_count: int = 0
    unit_count: int = 0
    required_downstream_inputs: tuple[str, ...] = ()
    file_refs_checksums: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class DeterministicArtifactBinding:
    """Binds a deterministic artifact for model input.

    A deterministic artifact binding links a concrete artifact (file, content)
    to the review chain, providing the facts extracted from it and its
    integrity checksums.
    """

    artifact_role: str  # "deterministic"
    artifact_phase: str  # "analysis" or "planning"
    job_id: str
    stage_index: int
    artifact_ref: str  # stable reference to the artifact
    artifact_revision_id: str | None = None
    content_checksum: str = ""
    input_checksum: str = ""
    source_evidence_refs: tuple[str, ...] = ()
    file_evidence_refs: tuple[str, ...] = ()
    profile_context: dict[str, Any] | None = None
    deterministic_facts: DeterministicAnalysisFacts | DeterministicPlanningFacts | None = None
    created_at: str = ""
    schema_version: str = "1.0.0"


# ── Validation: deterministic artifact ──────────────────────────────────


class DeterministicArtifactValidationError(ValueError):
    """Raised when a deterministic artifact fails contract validation."""


def validate_deterministic_artifact_binding(
    binding: DeterministicArtifactBinding,
) -> list[str]:
    """Validate a deterministic artifact binding. Returns list of failures.

    Fail-closed: any failure means the binding is invalid and must not be
    accepted for model input.
    """
    failures: list[str] = []

    if not binding.artifact_ref or not binding.artifact_ref.strip():
        failures.append("missing artifact_ref")
    if not binding.content_checksum or not binding.content_checksum.strip():
        failures.append("missing content_checksum")
    if not binding.job_id or not binding.job_id.strip():
        failures.append("missing job_id")
    if binding.artifact_phase not in (ArtifactPhase.ANALYSIS.value, ArtifactPhase.PLANNING.value):
        failures.append(
            f"unknown artifact_phase {binding.artifact_phase!r}; "
            f"must be 'analysis' or 'planning'"
        )
    if binding.artifact_role != "deterministic":
        failures.append(
            f"invalid artifact_role {binding.artifact_role!r}; must be 'deterministic'"
        )
    if binding.stage_index < 1 or binding.stage_index > 3:
        failures.append(f"stage_index {binding.stage_index} out of range [1,3]")

    if binding.deterministic_facts is None:
        failures.append("missing deterministic_facts")
    else:
        facts = binding.deterministic_facts
        if isinstance(facts, DeterministicAnalysisFacts):
            failures.extend(_validate_analysis_facts(facts))
        elif isinstance(facts, DeterministicPlanningFacts):
            failures.extend(_validate_planning_facts(facts))
        else:
            failures.append(
                f"unknown deterministic_facts type: {type(facts).__name__}"
            )

    return failures


def _validate_analysis_facts(facts: DeterministicAnalysisFacts) -> list[str]:
    failures: list[str] = []
    if not facts.detected_framework and not facts.detected_language and not facts.build_tool:
        failures.append(
            "deterministic Analysis facts must include at least one of "
            "detected_framework, detected_language, build_tool"
        )
    return failures


def _validate_planning_facts(facts: DeterministicPlanningFacts) -> list[str]:
    failures: list[str] = []
    if not facts.selected_migration_stages:
        failures.append("deterministic Planning facts must include selected_migration_stages")
    return failures


# ── AMF-255: Primary LLM contract ───────────────────────────────────────


@dataclass(frozen=True)
class PrimaryLLMInput:
    """Primary LLM input contract — backend-owned and artifact-bound.

    Must reference deterministic artifacts by ref and checksum.
    Must NOT include sandbox_path, argv, env, raw command, filesystem target,
    provider, endpoint, deployment, env ref, or user-supplied file paths.
    """

    deterministic_artifact_ref: str
    deterministic_artifact_checksum: str
    phase: str  # "analysis" or "planning"
    job_id: str
    stage_index: int

    source_profile: dict[str, Any] | None = None
    target_profile: dict[str, Any] | None = None
    allowed_user_comments: tuple[str, ...] = ()
    safe_artifact_preview_text: str | None = None


@dataclass(frozen=True)
class PrimaryLLMOutput:
    """Primary LLM output contract.

    Must include reasoning, risks, confidence, recommended next step,
    draft Markdown, and machine-readable metadata.
    Must NOT include execution instructions, runtime internals, or
    forbidden fields.
    """

    reasoning: str
    risks: tuple[str, ...]
    confidence: float
    recommended_next_step: str
    draft_markdown: str
    machine_readable_metadata: dict[str, Any] = field(default_factory=dict)
    output_checksum: str = ""


class PrimaryLLMOutputValidationError(ValueError):
    """Raised when primary LLM output fails validation."""


_PRIMARY_REQUIRED_FIELDS: tuple[str, ...] = (
    "reasoning",
    "risks",
    "confidence",
    "recommended_next_step",
    "draft_markdown",
)


def validate_primary_llm_input(input_: PrimaryLLMInput) -> list[str]:
    """Validate primary LLM input. Returns list of failures. Fail-closed."""
    failures: list[str] = []

    if not input_.deterministic_artifact_ref or not input_.deterministic_artifact_ref.strip():
        failures.append("missing deterministic_artifact_ref")
    if not input_.deterministic_artifact_checksum or not input_.deterministic_artifact_checksum.strip():
        failures.append("missing deterministic_artifact_checksum")
    if input_.phase not in (ArtifactPhase.ANALYSIS.value, ArtifactPhase.PLANNING.value):
        failures.append(f"unknown phase {input_.phase!r}; must be 'analysis' or 'planning'")
    if not input_.job_id or not input_.job_id.strip():
        failures.append("missing job_id")
    if input_.stage_index < 1 or input_.stage_index > 3:
        failures.append(f"stage_index {input_.stage_index} out of range [1,3]")

    failures.extend(_check_forbidden_fields(input_, "PrimaryLLMInput"))
    return failures


def validate_primary_llm_output(output: PrimaryLLMOutput) -> list[str]:
    """Validate primary LLM output. Returns list of failures. Fail-closed.

    Malformed output must fail closed:
    - missing reasoning, draft_markdown, or confidence -> invalid
    - unsupported recommended next step -> invalid
    - attempted execution instruction -> invalid
    - provider/runtime leak -> invalid
    """
    failures: list[str] = []

    missing = [f for f in _PRIMARY_REQUIRED_FIELDS if not _field_present(output, f)]
    for field_name in missing:
        failures.append(f"missing {field_name}")

    if not (0.0 <= output.confidence <= 1.0):
        failures.append(
            f"confidence {output.confidence} out of range [0.0, 1.0]"
        )

    if not output.draft_markdown or not output.draft_markdown.strip():
        failures.append("draft_markdown must not be empty")

    if not output.reasoning or not output.reasoning.strip():
        failures.append("reasoning must not be empty")

    failures.extend(_check_forbidden_fields(output, "PrimaryLLMOutput"))
    failures.extend(_check_execution_instruction(output))
    return failures


def _field_present(obj: Any, field_name: str) -> bool:
    value = getattr(obj, field_name, None)
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, tuple)) and len(value) == 0:
        return False
    return True


# ── AMF-256: Reviewer LLM contract ─────────────────────────────────────


@dataclass(frozen=True)
class ReviewerLLMInput:
    """Reviewer LLM input contract.

    Binds deterministic artifact and primary LLM output by exact checksums.
    Must reference both the deterministic artifact and the primary output
    it is reviewing.
    """

    deterministic_artifact_ref: str
    deterministic_artifact_checksum: str
    primary_output_ref: str
    primary_output_checksum: str
    primary_reasoning: str
    draft_markdown: str
    phase: str
    job_id: str
    stage_index: int
    policy_hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewerLLMOutput:
    """Reviewer LLM output contract.

    Binds its decision to exact deterministic and primary output checksums.
    Decision must be accept, reject, or request_revision.
    """

    decision: str  # accept | reject | request_revision
    notes: tuple[str, ...]
    confidence: float
    risks: tuple[str, ...]
    policy_concerns: tuple[str, ...]
    reviewed_artifact_checksum: str
    reviewed_primary_output_checksum: str
    reviewer_output_checksum: str = ""
    review_dimensions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewerValidationResult:
    """Structured result of reviewer validation against the contract."""

    ok: bool
    decision: str | None
    failures: tuple[str, ...]
    checksum_matched: bool
    deterministic_artifact_checksum: str
    primary_output_checksum: str
    reviewer_output_checksum: str


class ReviewerLLMOutputValidationError(ValueError):
    """Raised when reviewer LLM output fails validation."""


def validate_reviewer_llm_input(input_: ReviewerLLMInput) -> list[str]:
    """Validate reviewer LLM input. Returns list of failures. Fail-closed."""
    failures: list[str] = []

    if not input_.deterministic_artifact_ref or not input_.deterministic_artifact_ref.strip():
        failures.append("missing deterministic_artifact_ref")
    if not input_.deterministic_artifact_checksum or not input_.deterministic_artifact_checksum.strip():
        failures.append("missing deterministic_artifact_checksum")
    if not input_.primary_output_ref or not input_.primary_output_ref.strip():
        failures.append("missing primary_output_ref")
    if not input_.primary_output_checksum or not input_.primary_output_checksum.strip():
        failures.append("missing primary_output_checksum")
    if not input_.primary_reasoning or not input_.primary_reasoning.strip():
        failures.append("missing primary_reasoning")
    if not input_.draft_markdown or not input_.draft_markdown.strip():
        failures.append("missing draft_markdown")
    if input_.phase not in (ArtifactPhase.ANALYSIS.value, ArtifactPhase.PLANNING.value):
        failures.append(f"unknown phase {input_.phase!r}; must be 'analysis' or 'planning'")
    if not input_.job_id or not input_.job_id.strip():
        failures.append("missing job_id")
    if input_.stage_index < 1 or input_.stage_index > 3:
        failures.append(f"stage_index {input_.stage_index} out of range [1,3]")

    failures.extend(_check_forbidden_fields(input_, "ReviewerLLMInput"))
    return failures


def validate_reviewer_llm_output(output: ReviewerLLMOutput) -> list[str]:
    """Validate reviewer LLM output. Returns list of failures. Fail-closed.

    Reviewer validation fails closed when:
    - reviewer output missing or malformed
    - decision not in {accept, reject, request_revision}
    - confidence out of range
    - missing checksum fields
    """
    failures: list[str] = []

    if output.decision not in (
        ReviewerDecision.ACCEPT.value,
        ReviewerDecision.REJECT.value,
        ReviewerDecision.REQUEST_REVISION.value,
    ):
        failures.append(
            f"invalid decision {output.decision!r}; "
            f"must be accept, reject, or request_revision"
        )

    if not (0.0 <= output.confidence <= 1.0):
        failures.append(
            f"confidence {output.confidence} out of range [0.0, 1.0]"
        )

    if not output.reviewed_artifact_checksum or not output.reviewed_artifact_checksum.strip():
        failures.append("missing reviewed_artifact_checksum")
    if not output.reviewed_primary_output_checksum or not output.reviewed_primary_output_checksum.strip():
        failures.append("missing reviewed_primary_output_checksum")

    failures.extend(_check_forbidden_fields(output, "ReviewerLLMOutput"))
    return failures


# ── AMF-257: Reviewer Decision Matrix ──────────────────────────────────


@dataclass(frozen=True)
class ReviewerDecisionOutcome:
    decision: str
    ok: bool
    blocked: bool
    revision_required: bool
    reason: str
    checksum_matched: bool
    notes: tuple[str, ...] = ()
    confidence: float | None = None


# Canonical failed-closed outcome constant
_FAILED_CLOSED_OUTCOME = ReviewerDecisionOutcome(
    decision=ReviewerDecision.FAILED_CLOSED.value,
    ok=False,
    blocked=True,
    revision_required=False,
    reason="backend terminated in failed-closed state",
    checksum_matched=False,
)

_STALE_OUTCOME = ReviewerDecisionOutcome(
    decision=ReviewerDecision.STALE.value,
    ok=False,
    blocked=True,
    revision_required=False,
    reason="stale reviewer input: deterministic or primary artifact changed",
    checksum_matched=False,
)

_CHECKSUM_MISMATCH_OUTCOME = ReviewerDecisionOutcome(
    decision=ReviewerDecision.CHECKSUM_MISMATCH.value,
    ok=False,
    blocked=True,
    revision_required=False,
    reason="checksum mismatch: reviewer output not bound to exact artifacts",
    checksum_matched=False,
)

_REVIEWER_FAILED_OUTCOME = ReviewerDecisionOutcome(
    decision=ReviewerDecision.REVIEWER_FAILED.value,
    ok=False,
    blocked=True,
    revision_required=False,
    reason="reviewer LLM execution failed",
    checksum_matched=False,
)


def resolve_reviewer_decision(
    reviewer_output: ReviewerLLMOutput | None,
    deterministic_artifact_checksum: str,
    primary_output_checksum: str,
) -> ReviewerDecisionOutcome:
    if reviewer_output is None:
        return _FAILED_CLOSED_OUTCOME

    output_failures = validate_reviewer_llm_output(reviewer_output)
    if output_failures:
        return ReviewerDecisionOutcome(
            decision=ReviewerDecision.MALFORMED.value,
            ok=False,
            blocked=True,
            revision_required=False,
            reason="malformed reviewer output: " + "; ".join(output_failures),
            checksum_matched=False,
            notes=reviewer_output.notes if reviewer_output else (),
        )

    binding_failures = validate_checksum_binding(
        deterministic_artifact_checksum, primary_output_checksum, reviewer_output
    )
    checksum_matched = len(binding_failures) == 0
    if not checksum_matched:
        return ReviewerDecisionOutcome(
            decision=ReviewerDecision.CHECKSUM_MISMATCH.value,
            ok=False,
            blocked=True,
            revision_required=False,
            reason="; ".join(binding_failures),
            checksum_matched=False,
            notes=reviewer_output.notes,
            confidence=reviewer_output.confidence,
        )

    decision = reviewer_output.decision
    if decision == ReviewerDecision.ACCEPT.value:
        return ReviewerDecisionOutcome(
            decision=decision,
            ok=True,
            blocked=False,
            revision_required=False,
            reason="reviewer accepted the output",
            checksum_matched=True,
            notes=reviewer_output.notes,
            confidence=reviewer_output.confidence,
        )
    elif decision == ReviewerDecision.REJECT.value:
        return ReviewerDecisionOutcome(
            decision=decision,
            ok=False,
            blocked=True,
            revision_required=False,
            reason="reviewer rejected the output: " + "; ".join(reviewer_output.notes),
            checksum_matched=True,
            notes=reviewer_output.notes,
            confidence=reviewer_output.confidence,
        )
    elif decision == ReviewerDecision.REQUEST_REVISION.value:
        return ReviewerDecisionOutcome(
            decision=decision,
            ok=False,
            blocked=True,
            revision_required=True,
            reason="reviewer requested revision: " + "; ".join(reviewer_output.notes),
            checksum_matched=True,
            notes=reviewer_output.notes,
            confidence=reviewer_output.confidence,
        )
    else:
        return ReviewerDecisionOutcome(
            decision=ReviewerDecision.MALFORMED.value,
            ok=False,
            blocked=True,
            revision_required=False,
            reason=f"unknown reviewer decision: {decision!r}",
            checksum_matched=True,
            notes=reviewer_output.notes,
            confidence=reviewer_output.confidence,
        )


def resolve_stale_decision() -> ReviewerDecisionOutcome:
    return _STALE_OUTCOME


def resolve_reviewer_failed_decision() -> ReviewerDecisionOutcome:
    return _REVIEWER_FAILED_OUTCOME


def resolve_failed_closed_decision(reason: str = "") -> ReviewerDecisionOutcome:
    if reason:
        return ReviewerDecisionOutcome(
            decision=ReviewerDecision.FAILED_CLOSED.value,
            ok=False,
            blocked=True,
            revision_required=False,
            reason=reason,
            checksum_matched=False,
        )
    return _FAILED_CLOSED_OUTCOME


def is_decision_failed_closed(outcome: ReviewerDecisionOutcome) -> bool:
    return outcome.decision in (
        ReviewerDecision.FAILED_CLOSED.value,
        ReviewerDecision.MALFORMED.value,
        ReviewerDecision.STALE.value,
        ReviewerDecision.CHECKSUM_MISMATCH.value,
        ReviewerDecision.REVIEWER_FAILED.value,
    )


def can_produce_final_artifact(outcome: ReviewerDecisionOutcome) -> bool:
    return outcome.ok and not outcome.blocked and outcome.checksum_matched


# ── AMF-258: Final Reviewed Markdown Artifact Schema ───────────────────


@dataclass(frozen=True)
class FinalMarkdownMetadata:
    job_id: str
    phase: str
    stage_index: int
    source_profile: str | None = None
    target_profile: str | None = None
    deterministic_artifact_ref: str = ""
    deterministic_artifact_checksum: str = ""
    primary_output_checksum: str = ""
    reviewer_output_checksum: str = ""
    review_decision: str = ""
    review_confidence: float | None = None
    final_markdown_checksum: str = ""
    created_at: str = ""
    schema_version: str = "2.0.0"


@dataclass(frozen=True)
class FinalReviewedMarkdown:
    summary: str
    inputs_used: str
    deterministic_findings: str
    file_names: tuple[str, ...]
    primary_reasoning: str
    reviewer_notes: str
    risks: tuple[str, ...]
    confidence: float
    recommended_next_step: str
    metadata: FinalMarkdownMetadata

    safe_artifact_refs: tuple[str, ...] = ()
    markdown_body: str = ""


_FINAL_MARKDOWN_REQUIRED_SECTIONS: tuple[str, ...] = (
    "summary",
    "inputs_used",
    "deterministic_findings",
    "file_names",
    "primary_reasoning",
    "reviewer_notes",
    "risks",
    "confidence",
    "recommended_next_step",
    "metadata",
)

_FINAL_MARKDOWN_REQUIRED_METADATA_KEYS: tuple[str, ...] = (
    "deterministic_artifact_checksum",
    "primary_output_checksum",
    "reviewer_output_checksum",
    "review_decision",
)


def _safe_file_names(file_names: tuple[str, ...]) -> bool:
    forbidden = {"sandbox", "argv", "env", "provider", "endpoint", "deployment"}
    for name in file_names:
        lower = name.lower().replace("\\", "/")
        for f in forbidden:
            if f in lower:
                return False
    return True


def validate_final_markdown(artifact: FinalReviewedMarkdown) -> list[str]:
    failures: list[str] = []

    missing_sections = [
        s for s in _FINAL_MARKDOWN_REQUIRED_SECTIONS
        if not _field_present(artifact, s)
    ]
    for section in missing_sections:
        failures.append(f"missing required section: {section}")

    if not artifact.metadata.deterministic_artifact_checksum.strip():
        failures.append("missing deterministic_artifact_checksum in metadata")
    if not artifact.metadata.primary_output_checksum.strip():
        failures.append("missing primary_output_checksum in metadata")
    if not artifact.metadata.reviewer_output_checksum.strip():
        failures.append("missing reviewer_output_checksum in metadata")
    if artifact.metadata.review_decision not in (ReviewerDecision.ACCEPT.value,):
        failures.append(
            f"final markdown requires accepted reviewer decision, "
            f"got {artifact.metadata.review_decision!r}"
        )
    if not (0.0 <= artifact.confidence <= 1.0):
        failures.append(f"confidence {artifact.confidence} out of range [0.0, 1.0]")

    if not _safe_file_names(artifact.file_names):
        failures.append("file_names contain forbidden path components")

    return failures


def compute_final_markdown_checksum(artifact: FinalReviewedMarkdown) -> str:
    """Canonical checksum of the final artifact envelope.

    Hashes required sections, safe_artifact_refs, and all metadata fields
    EXCEPT the final_markdown_checksum field itself to avoid circularity.

    Uses json.dumps with sort_keys, compact separators, and ensure_ascii
    for deterministic output, then sha256 over the UTF-8 bytes.
    """
    md = artifact.metadata
    meta_payload: dict[str, Any] = {
        "job_id": md.job_id,
        "phase": md.phase,
        "stage_index": md.stage_index,
        "source_profile": md.source_profile,
        "target_profile": md.target_profile,
        "deterministic_artifact_ref": md.deterministic_artifact_ref,
        "deterministic_artifact_checksum": md.deterministic_artifact_checksum,
        "primary_output_checksum": md.primary_output_checksum,
        "reviewer_output_checksum": md.reviewer_output_checksum,
        "review_decision": md.review_decision,
        "review_confidence": md.review_confidence,
        "created_at": md.created_at,
        "schema_version": md.schema_version,
    }
    payload: dict[str, Any] = {
        "summary": artifact.summary,
        "inputs_used": artifact.inputs_used,
        "deterministic_findings": artifact.deterministic_findings,
        "file_names": list(artifact.file_names),
        "primary_reasoning": artifact.primary_reasoning,
        "reviewer_notes": artifact.reviewer_notes,
        "risks": list(artifact.risks),
        "confidence": artifact.confidence,
        "recommended_next_step": artifact.recommended_next_step,
        "safe_artifact_refs": list(artifact.safe_artifact_refs),
        "metadata": meta_payload,
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()


# ── AMF-260: Metadata and Checksum Binding ─────────────────────────────


@dataclass(frozen=True)
class CompleteChecksumChain:
    deterministic_artifact_checksum: str
    primary_input_checksum: str
    primary_output_checksum: str
    reviewer_input_checksum: str
    reviewer_output_checksum: str
    final_markdown_checksum: str

    job_id: str
    phase: str
    stage_index: int
    source_profile: str | None = None
    target_profile: str | None = None
    review_decision: str = ""
    review_confidence: float | None = None
    artifact_ref: str = ""


class ChecksumChainValidationError(ValueError):
    pass


def validate_complete_checksum_chain(chain: CompleteChecksumChain) -> list[str]:
    failures: list[str] = []

    if not chain.deterministic_artifact_checksum or not chain.deterministic_artifact_checksum.strip():
        failures.append("missing deterministic_artifact_checksum")
    if not chain.primary_input_checksum or not chain.primary_input_checksum.strip():
        failures.append("missing primary_input_checksum")
    if not chain.primary_output_checksum or not chain.primary_output_checksum.strip():
        failures.append("missing primary_output_checksum")
    if not chain.reviewer_input_checksum or not chain.reviewer_input_checksum.strip():
        failures.append("missing reviewer_input_checksum")
    if not chain.reviewer_output_checksum or not chain.reviewer_output_checksum.strip():
        failures.append("missing reviewer_output_checksum")
    if not chain.final_markdown_checksum or not chain.final_markdown_checksum.strip():
        failures.append("missing final_markdown_checksum")
    if not chain.job_id or not chain.job_id.strip():
        failures.append("missing job_id")
    if chain.phase not in (ArtifactPhase.ANALYSIS.value, ArtifactPhase.PLANNING.value):
        failures.append(f"unknown phase {chain.phase!r}; must be 'analysis' or 'planning'")
    if chain.stage_index < 1 or chain.stage_index > 3:
        failures.append(f"stage_index {chain.stage_index} out of range [1,3]")
    if chain.review_decision and chain.review_decision not in (
        ReviewerDecision.ACCEPT.value,
    ):
        failures.append(
            f"checksum chain review_decision must be 'accept', "
            f"got {chain.review_decision!r}"
        )

    return failures


def validate_checksum_chain_against_reference(
    chain: CompleteChecksumChain,
    expected_job_id: str,
    expected_phase: str,
    expected_stage_index: int,
) -> list[str]:
    failures: list[str] = []
    if chain.job_id != expected_job_id:
        failures.append(
            f"foreign job_id: {chain.job_id!r} != {expected_job_id!r}"
        )
    if chain.phase != expected_phase:
        failures.append(
            f"wrong phase: {chain.phase!r} != {expected_phase!r}"
        )
    if chain.stage_index != expected_stage_index:
        failures.append(
            f"wrong stage_index: {chain.stage_index} != {expected_stage_index}"
        )
    return failures


METADATA_SAFE_FIELDS: frozenset[str] = frozenset({
    "job_id",
    "phase",
    "stage_index",
    "source_profile",
    "target_profile",
    "deterministic_artifact_ref",
    "deterministic_artifact_checksum",
    "primary_output_checksum",
    "reviewer_output_checksum",
    "review_decision",
    "review_confidence",
    "final_markdown_checksum",
    "artifact_ref",
    "created_at",
    "schema_version",
})

_METADATA_FORBIDDEN_FIELDS: frozenset[str] = frozenset({
    "provider",
    "endpoint",
    "deployment",
    "sandbox_path",
    "argv",
    "env",
    "env_ref",
    "raw_command",
    "filesystem_target",
    "user_supplied_file_path",
})


def validate_metadata_safety(metadata: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    _collect_forbidden_keys(metadata, "root", failures)
    return failures


def _collect_forbidden_keys(value: Any, path: str, failures: list[str]) -> None:
    if isinstance(value, dict):
        for key, val in value.items():
            key_lower = str(key).lower()
            if key_lower in _METADATA_FORBIDDEN_FIELDS:
                failures.append(f"metadata contains forbidden field {key!r} at {path}")
            next_path = f"{path}.{key}"
            _collect_forbidden_keys(val, next_path, failures)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for i, item in enumerate(value):
            next_path = f"{path}[{i}]"
            _collect_forbidden_keys(item, next_path, failures)
    elif hasattr(value, "__dataclass_fields__"):
        for field_name in value.__dataclass_fields__:
            field_val = getattr(value, field_name, None)
            if field_name.lower() in _METADATA_FORBIDDEN_FIELDS:
                failures.append(f"metadata contains forbidden field {field_name!r} at {path}")
            next_path = f"{path}.{field_name}"
            _collect_forbidden_keys(field_val, next_path, failures)


def safe_metadata_dict(chain: CompleteChecksumChain) -> dict[str, Any]:
    return {
        "job_id": chain.job_id,
        "phase": chain.phase,
        "stage_index": chain.stage_index,
        "source_profile": chain.source_profile,
        "target_profile": chain.target_profile,
        "artifact_ref": chain.artifact_ref,
        "deterministic_artifact_checksum": chain.deterministic_artifact_checksum,
        "primary_input_checksum": chain.primary_input_checksum,
        "primary_output_checksum": chain.primary_output_checksum,
        "reviewer_input_checksum": chain.reviewer_input_checksum,
        "reviewer_output_checksum": chain.reviewer_output_checksum,
        "final_markdown_checksum": chain.final_markdown_checksum,
        "review_decision": chain.review_decision,
        "review_confidence": chain.review_confidence,
    }


def is_checksum_stale(
    reference_checksum: str,
    current_checksum: str,
) -> bool:
    return bool(reference_checksum and current_checksum
                and reference_checksum != current_checksum)


# ── AMF-259: Retry and Revision Behavior ───────────────────────────────


@dataclass(frozen=True)
class RevisionRequest:
    job_id: str
    phase: str
    stage_index: int
    artifact_ref: str
    previous_deterministic_checksum: str
    previous_primary_output_checksum: str
    previous_reviewer_output_checksum: str
    reviewer_decision: str
    reviewer_notes: tuple[str, ...]
    revision_number: int
    source_profile: str | None = None
    target_profile: str | None = None
    user_comments: tuple[str, ...] = ()
    revision_reason: str = ""


@dataclass(frozen=True)
class ArtifactRejectionResult:
    job_id: str
    blocked: bool
    revision_required: bool
    reviewer_decision: str
    reviewer_notes: tuple[str, ...]
    user_comments: tuple[str, ...]
    deterministic_checksum: str
    primary_output_checksum: str
    reviewer_output_checksum: str
    rejection_reason: str = ""


@dataclass(frozen=True)
class RevisionResult:
    revision_id: str
    job_id: str
    revision_number: int
    revision_state: str
    previous_deterministic_checksum: str
    previous_reviewer_output_checksum: str
    new_deterministic_checksum: str
    new_primary_output_checksum: str
    new_reviewer_output_checksum: str
    is_accepted: bool = False
    failure_reason: str = ""


class ReviewRetryLimits:
    MAX_REVISIONS: int = 5
    MAX_RETRIES_PER_REVISION: int = 3


def build_artifact_rejection_result(
    job_id: str,
    outcome: ReviewerDecisionOutcome,
    deterministic_checksum: str,
    primary_output_checksum: str,
    reviewer_output_checksum: str,
    user_comments: tuple[str, ...] = (),
) -> ArtifactRejectionResult:
    return ArtifactRejectionResult(
        job_id=job_id,
        blocked=outcome.blocked,
        revision_required=outcome.revision_required,
        reviewer_decision=outcome.decision,
        reviewer_notes=outcome.notes,
        user_comments=user_comments,
        deterministic_checksum=deterministic_checksum,
        primary_output_checksum=primary_output_checksum,
        reviewer_output_checksum=reviewer_output_checksum,
        rejection_reason=outcome.reason,
    )


def validate_revision_request(request: RevisionRequest) -> list[str]:
    failures: list[str] = []
    if not request.job_id or not request.job_id.strip():
        failures.append("missing job_id")
    if request.phase not in (ArtifactPhase.ANALYSIS.value, ArtifactPhase.PLANNING.value):
        failures.append(f"unknown phase {request.phase!r}")
    if request.stage_index < 1 or request.stage_index > 3:
        failures.append(f"stage_index {request.stage_index} out of range [1,3]")
    if not request.artifact_ref or not request.artifact_ref.strip():
        failures.append("missing artifact_ref")
    if not request.previous_deterministic_checksum.strip():
        failures.append("missing previous_deterministic_checksum")
    if not request.previous_primary_output_checksum.strip():
        failures.append("missing previous_primary_output_checksum")
    if not request.previous_reviewer_output_checksum.strip():
        failures.append("missing previous_reviewer_output_checksum")
    if request.reviewer_decision not in (
        ReviewerDecision.REJECT.value,
        ReviewerDecision.REQUEST_REVISION.value,
    ):
        failures.append(
            f"revision requires reject or request_revision, "
            f"got {request.reviewer_decision!r}"
        )
    if request.revision_number < 1:
        failures.append(f"revision_number {request.revision_number} must be >= 1")
    if request.revision_number > ReviewRetryLimits.MAX_REVISIONS:
        failures.append(
            f"revision_number {request.revision_number} exceeds "
            f"limit {ReviewRetryLimits.MAX_REVISIONS}"
        )
    return failures


def is_revision_idempotent(
    existing: RevisionRequest,
    new_request: RevisionRequest,
) -> bool:
    return (
        existing.job_id == new_request.job_id
        and existing.phase == new_request.phase
        and existing.stage_index == new_request.stage_index
        and existing.previous_deterministic_checksum == new_request.previous_deterministic_checksum
        and existing.previous_primary_output_checksum == new_request.previous_primary_output_checksum
        and existing.previous_reviewer_output_checksum == new_request.previous_reviewer_output_checksum
        and existing.revision_number == new_request.revision_number
    )


def is_checkpoint_acceptance_blocked(
    outcome: ReviewerDecisionOutcome,
) -> bool:
    return (
        outcome.blocked
        or not outcome.ok
        or not outcome.checksum_matched
        or is_decision_failed_closed(outcome)
    )


def validate_runtime_review_chain_result(
    result: dict[str, Any],
    *,
    phase: str,
    stage_index: int,
    expected_job_id: str | None = None,
) -> list[str]:
    """Validate Analysis/Planning runtime output before checkpoint use.

    The orchestrator result must prove the F2 chain:
    deterministic artifact -> primary output -> reviewer accept ->
    checksum-bound final reviewed Markdown.
    """
    failures: list[str] = []
    if phase not in (ArtifactPhase.ANALYSIS.value, ArtifactPhase.PLANNING.value):
        return [f"unknown phase {phase!r}"]

    review_chain = result.get("review_chain")
    if not isinstance(review_chain, dict):
        return ["missing review_chain"]

    deterministic_checksum = _text(review_chain.get("deterministic_artifact_checksum"))
    primary_checksum = _text(review_chain.get("primary_output_checksum"))
    reviewer_checksum = _text(review_chain.get("reviewer_output_checksum"))
    final_checksum = _text(review_chain.get("final_markdown_checksum"))
    final_ref = _text(review_chain.get("final_markdown_ref"))
    primary_ref = _text(review_chain.get("primary_output_ref"))
    decision = _text(review_chain.get("reviewer_decision"))
    job_id = _text(review_chain.get("job_id")) or _text(result.get("job_id")) or "runtime-job"

    chain = CompleteChecksumChain(
        deterministic_artifact_checksum=deterministic_checksum,
        primary_input_checksum=_text(review_chain.get("primary_input_checksum")) or "runtime-input",
        primary_output_checksum=primary_checksum,
        reviewer_input_checksum=_text(review_chain.get("reviewer_input_checksum")) or "runtime-reviewer-input",
        reviewer_output_checksum=reviewer_checksum,
        final_markdown_checksum=final_checksum,
        job_id=job_id,
        phase=phase,
        stage_index=stage_index,
        source_profile=_text(review_chain.get("source_profile")) or None,
        target_profile=_text(review_chain.get("target_profile")) or None,
        review_decision=decision,
        review_confidence=review_chain.get("review_confidence") if isinstance(review_chain.get("review_confidence"), (int, float)) else None,
        artifact_ref=final_ref,
    )
    failures.extend(validate_complete_checksum_chain(chain))
    if expected_job_id and job_id != expected_job_id:
        failures.append(f"foreign job_id: {job_id!r} != {expected_job_id!r}")

    if decision != ReviewerDecision.ACCEPT.value:
        failures.append(f"reviewer decision must be accept, got {decision!r}")

    reviewed_artifact_checksum = _text(review_chain.get("reviewed_artifact_checksum")) or deterministic_checksum
    reviewed_primary_checksum = _text(review_chain.get("reviewed_primary_output_checksum")) or primary_checksum
    reviewer_output = ReviewerLLMOutput(
        decision=decision,
        notes=tuple(str(n) for n in review_chain.get("reviewer_notes", ()) if str(n).strip())
        if isinstance(review_chain.get("reviewer_notes", ()), (list, tuple))
        else (_text(review_chain.get("reviewer_notes")),) if _text(review_chain.get("reviewer_notes")) else (),
        confidence=float(review_chain.get("review_confidence", 1.0) or 0.0),
        risks=(),
        policy_concerns=(),
        reviewed_artifact_checksum=reviewed_artifact_checksum,
        reviewed_primary_output_checksum=reviewed_primary_checksum,
        reviewer_output_checksum=reviewer_checksum,
    )
    outcome = resolve_reviewer_decision(
        reviewer_output,
        deterministic_checksum,
        primary_checksum,
    )
    if not can_produce_final_artifact(outcome):
        failures.append(outcome.reason)

    artifact_refs = result.get("artifact_refs") if isinstance(result.get("artifact_refs"), dict) else {}
    final_artifact_ref = _text(artifact_refs.get("final_reviewed_markdown"))
    if not final_artifact_ref:
        failures.append("missing final_reviewed_markdown artifact ref")
    elif final_ref and final_artifact_ref != final_ref:
        failures.append("final reviewed Markdown artifact ref does not match review_chain")

    primary_artifact_ref = _text(artifact_refs.get("primary_llm_output"))
    if primary_artifact_ref and not final_artifact_ref:
        failures.append("raw primary output cannot satisfy downstream artifact resolution")
    if primary_artifact_ref and final_artifact_ref and primary_artifact_ref == final_artifact_ref:
        failures.append("raw primary output cannot be used as final reviewed Markdown")
    if primary_ref and final_ref and primary_ref == final_ref:
        failures.append("review_chain primary output ref cannot equal final reviewed Markdown ref")
    if primary_checksum and final_checksum and primary_checksum == final_checksum:
        failures.append("primary output checksum cannot equal final reviewed Markdown checksum")

    return failures


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


# ── Checksum binding ───────────────────────────────────────────────────


class ChecksumBindingValidationError(ValueError):
    """Raised when checksum binding fails."""


def validate_checksum_binding(
    deterministic_artifact_checksum: str,
    primary_output_checksum: str,
    reviewer_output: ReviewerLLMOutput,
) -> list[str]:
    """Validate that reviewer output is checksum-bound to exact artifacts.

    Returns list of failures. Fail-closed.
    """
    failures: list[str] = []

    if reviewer_output.reviewed_artifact_checksum != deterministic_artifact_checksum:
        failures.append(
            f"checksum mismatch on deterministic artifact: "
            f"reviewer recorded {reviewer_output.reviewed_artifact_checksum!r} "
            f"but expected {deterministic_artifact_checksum!r}"
        )

    if reviewer_output.reviewed_primary_output_checksum != primary_output_checksum:
        failures.append(
            f"checksum mismatch on primary output: "
            f"reviewer recorded {reviewer_output.reviewed_primary_output_checksum!r} "
            f"but expected {primary_output_checksum!r}"
        )

    return failures


def validate_reviewed_output_contract(
    deterministic_artifact_checksum: str,
    primary_output_checksum: str,
    reviewer_output: ReviewerLLMOutput,
) -> ReviewerValidationResult:
    """Full fail-closed reviewer validation: output shape + checksum binding.

    Returns a ReviewerValidationResult with ok=False if:
    - reviewer output is malformed
    - reviewer rejects or requests revision
    - checksum mismatch
    - reviewer decision not bound to exact primary output
    """
    failures: list[str] = []

    output_failures = validate_reviewer_llm_output(reviewer_output)
    failures.extend(output_failures)

    if output_failures:
        return ReviewerValidationResult(
            ok=False,
            decision=reviewer_output.decision if reviewer_output else None,
            failures=tuple(failures),
            checksum_matched=False,
            deterministic_artifact_checksum=deterministic_artifact_checksum,
            primary_output_checksum=primary_output_checksum,
            reviewer_output_checksum=reviewer_output.reviewer_output_checksum if reviewer_output else "",
        )

    binding_failures = validate_checksum_binding(
        deterministic_artifact_checksum,
        primary_output_checksum,
        reviewer_output,
    )
    failures.extend(binding_failures)
    checksum_matched = len(binding_failures) == 0

    decision_ok = reviewer_output.decision == ReviewerDecision.ACCEPT.value

    if reviewer_output.decision == ReviewerDecision.REJECT.value:
        failures.append("reviewer rejected the output")
    elif reviewer_output.decision == ReviewerDecision.REQUEST_REVISION.value:
        failures.append("reviewer requested revision")

    ok = decision_ok and checksum_matched and len(failures) == 0

    return ReviewerValidationResult(
        ok=ok,
        decision=reviewer_output.decision,
        failures=tuple(failures),
        checksum_matched=checksum_matched,
        deterministic_artifact_checksum=deterministic_artifact_checksum,
        primary_output_checksum=primary_output_checksum,
        reviewer_output_checksum=reviewer_output.reviewer_output_checksum,
    )


# ── Forbidden field detection ──────────────────────────────────────────


def _check_forbidden_fields(obj: Any, label: str) -> list[str]:
    """Scan a dataclass instance for forbidden top-level fields.

    Returns list of failure messages if forbidden fields are found.
    """
    failures: list[str] = []
    if not hasattr(obj, "__dataclass_fields__"):
        return failures

    for field_name in obj.__dataclass_fields__:
        if field_name in _FORBIDDEN_TOP_LEVEL_KEYS:
            value = getattr(obj, field_name, None)
            if value is not None:
                failures.append(
                    f"{label} contains forbidden field {field_name!r}"
                )

    for field_name in obj.__dataclass_fields__:
        value = getattr(obj, field_name, None)
        if isinstance(value, dict):
            for key in value:
                for forbidden_substr in _FORBIDDEN_DICT_KEY_SUBSTRINGS:
                    if forbidden_substr in str(key).lower():
                        if _has_value(value[key]):
                            failures.append(
                                f"{label} field {field_name!r} contains "
                                f"forbidden dict key {key!r}"
                            )

    return failures


def _check_execution_instruction(output: PrimaryLLMOutput) -> list[str]:
    """Check primary LLM output for execution instructions.

    Returns list of failure messages.
    """
    failures: list[str] = []
    text = " ".join(
        [
            output.reasoning or "",
            output.recommended_next_step or "",
            output.draft_markdown or "",
        ]
    ).lower()

    execution_signals = [
        "execute command",
        "run command",
        "apply patch",
        "modify file",
        "write to disk",
        "delete file",
        "rm -rf",
        "sudo ",
    ]
    for signal in execution_signals:
        if signal in text:
            failures.append(
                f"primary LLM output contains execution instruction: {signal!r}"
            )
    return failures


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, tuple, dict)) and len(value) == 0:
        return False
    return True


# ── Checksum helpers ───────────────────────────────────────────────────


def compute_primary_output_checksum(output: PrimaryLLMOutput) -> str:
    """Compute checksum for primary LLM output (without output_checksum field)."""
    payload = {
        "reasoning": output.reasoning,
        "risks": list(output.risks),
        "confidence": output.confidence,
        "recommended_next_step": output.recommended_next_step,
        "draft_markdown": output.draft_markdown,
        "machine_readable_metadata": output.machine_readable_metadata,
    }
    return sha256_canonical_json(payload)


def compute_reviewer_output_checksum(output: ReviewerLLMOutput) -> str:
    """Compute checksum for reviewer LLM output (without reviewer_output_checksum)."""
    payload = {
        "decision": output.decision,
        "notes": list(output.notes),
        "confidence": output.confidence,
        "risks": list(output.risks),
        "policy_concerns": list(output.policy_concerns),
        "reviewed_artifact_checksum": output.reviewed_artifact_checksum,
        "reviewed_primary_output_checksum": output.reviewed_primary_output_checksum,
        "review_dimensions": output.review_dimensions,
    }
    return sha256_canonical_json(payload)
