from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from migration_factory.control_tower.application.v2_assistant_model_client import (
    V2AssistantModelClient,
)
from migration_factory.control_tower.application.v2_model_role_router import V2ModelRole
from migration_factory.control_tower.application.v2_review_chain_contracts import (
    CompleteChecksumChain,
    FinalMarkdownMetadata,
    FinalReviewedMarkdown,
    PrimaryLLMInput,
    PrimaryLLMOutput,
    ReviewerDecision,
    ReviewerLLMInput,
    ReviewerLLMOutput,
    compute_final_markdown_checksum,
    compute_primary_output_checksum,
    compute_reviewer_output_checksum,
    safe_metadata_dict,
    validate_complete_checksum_chain,
    validate_final_markdown,
    validate_primary_llm_input,
    validate_primary_llm_output,
    validate_reviewed_output_contract,
    validate_reviewer_llm_input,
    validate_reviewer_llm_output,
    validate_runtime_review_chain_result,
)
from migration_factory.control_tower.domain.checksums import (
    sha256_canonical_json,
    utc_now_text,
)
from migration_factory.orchestrator.state import MigrationState


class ReviewChainProductionError(RuntimeError):
    pass


def produce_phase_review_chain(
    state: MigrationState,
    *,
    phase: str,
    stage_index: int,
    artifact_refs: dict[str, str],
    deterministic_facts: dict[str, Any],
    warnings: list[str] | None = None,
    model_client: V2AssistantModelClient | None = None,
) -> dict[str, Any]:
    """Produce the F2 model-reviewed artifact chain for Analysis/Planning.

    This runs after deterministic artifacts are written. It fails closed when
    either model call is unavailable, malformed, or rejected.
    """

    if phase not in {"analysis", "planning"}:
        raise ReviewChainProductionError(f"unsupported review-chain phase {phase!r}")

    job_id = str(state.get("job_id") or state.get("run_id") or "").strip()
    run_id = str(state.get("run_id") or job_id).strip()
    modernized_path = Path(str(state.get("modernized_app_path") or ""))
    if not run_id or not modernized_path:
        raise ReviewChainProductionError("missing run_id or modernized_app_path")

    output_dir = modernized_path / ".migration" / "runs" / run_id / phase / "review_chain"
    output_dir.mkdir(parents=True, exist_ok=True)

    deterministic_ref = _choose_deterministic_ref(phase, artifact_refs)
    deterministic_payload = _build_deterministic_payload(
        state=state,
        phase=phase,
        stage_index=stage_index,
        artifact_refs=artifact_refs,
        deterministic_facts=deterministic_facts,
    )
    deterministic_checksum = sha256_canonical_json(deterministic_payload)
    deterministic_path = output_dir / "deterministic_artifact.json"
    _write_json(deterministic_path, deterministic_payload)

    primary_input = PrimaryLLMInput(
        deterministic_artifact_ref=deterministic_ref or str(deterministic_path),
        deterministic_artifact_checksum=deterministic_checksum,
        phase=phase,
        job_id=job_id,
        stage_index=stage_index,
        source_profile=_profile_dict(state, "source_profile"),
        target_profile=_profile_dict(state, "target_profile"),
        safe_artifact_preview_text=_safe_preview(deterministic_payload),
    )
    failures = validate_primary_llm_input(primary_input)
    if failures:
        raise ReviewChainProductionError("invalid primary input: " + "; ".join(failures))
    primary_input_checksum = sha256_canonical_json(asdict(primary_input))

    client = model_client or V2AssistantModelClient()
    primary_result = client.answer_with_role(
        role=V2ModelRole.PROPOSER,
        prompt=_primary_prompt(primary_input),
        fallback="Primary model unavailable; reviewed phase artifact cannot be produced.",
    )
    if not primary_result.success:
        raise ReviewChainProductionError(
            f"primary model failed closed: {primary_result.failure_reason or primary_result.model_status}"
        )

    primary_output = _coerce_primary_output(
        content=primary_result.content,
        phase=phase,
        warnings=warnings or [],
    )
    primary_failures = validate_primary_llm_output(primary_output)
    if primary_failures:
        raise ReviewChainProductionError(
            "invalid primary output: " + "; ".join(primary_failures)
        )
    primary_checksum = compute_primary_output_checksum(primary_output)
    primary_output = PrimaryLLMOutput(
        reasoning=primary_output.reasoning,
        risks=primary_output.risks,
        confidence=primary_output.confidence,
        recommended_next_step=primary_output.recommended_next_step,
        draft_markdown=primary_output.draft_markdown,
        machine_readable_metadata=primary_output.machine_readable_metadata,
        output_checksum=primary_checksum,
    )
    primary_path = output_dir / "primary_llm_output.json"
    _write_json(primary_path, asdict(primary_output))

    reviewer_input = ReviewerLLMInput(
        deterministic_artifact_ref=primary_input.deterministic_artifact_ref,
        deterministic_artifact_checksum=deterministic_checksum,
        primary_output_ref=str(primary_path),
        primary_output_checksum=primary_checksum,
        primary_reasoning=primary_output.reasoning,
        draft_markdown=primary_output.draft_markdown,
        phase=phase,
        job_id=job_id,
        stage_index=stage_index,
        policy_hints=(
            "A different model must review the primary output.",
            "Accept only if the draft is grounded in the deterministic artifact checksum.",
            "Reject execution instructions or unsafe runtime fields.",
        ),
    )
    reviewer_input_failures = validate_reviewer_llm_input(reviewer_input)
    if reviewer_input_failures:
        raise ReviewChainProductionError(
            "invalid reviewer input: " + "; ".join(reviewer_input_failures)
        )
    reviewer_input_checksum = sha256_canonical_json(asdict(reviewer_input))

    reviewer_result = client.answer_with_role(
        role=V2ModelRole.REVIEWER,
        prompt=_reviewer_prompt(reviewer_input),
        fallback="Reviewer model unavailable; reviewed phase artifact cannot be produced.",
    )
    if not reviewer_result.success:
        raise ReviewChainProductionError(
            f"reviewer model failed closed: {reviewer_result.failure_reason or reviewer_result.model_status}"
        )

    reviewer_output = _coerce_reviewer_output(
        content=reviewer_result.content,
        deterministic_checksum=deterministic_checksum,
        primary_checksum=primary_checksum,
    )
    reviewer_failures = validate_reviewer_llm_output(reviewer_output)
    if reviewer_failures:
        raise ReviewChainProductionError(
            "invalid reviewer output: " + "; ".join(reviewer_failures)
        )
    reviewed_result = validate_reviewed_output_contract(
        deterministic_checksum,
        primary_checksum,
        reviewer_output,
    )
    if not reviewed_result.ok:
        raise ReviewChainProductionError(
            "reviewer checksum validation failed: "
            + "; ".join(reviewed_result.failures)
        )
    if reviewer_output.decision != ReviewerDecision.ACCEPT.value:
        raise ReviewChainProductionError(
            f"reviewer decision failed closed: {reviewer_output.decision}"
        )
    reviewer_checksum = compute_reviewer_output_checksum(reviewer_output)
    reviewer_output = ReviewerLLMOutput(
        decision=reviewer_output.decision,
        notes=reviewer_output.notes,
        confidence=reviewer_output.confidence,
        risks=reviewer_output.risks,
        policy_concerns=reviewer_output.policy_concerns,
        reviewed_artifact_checksum=reviewer_output.reviewed_artifact_checksum,
        reviewed_primary_output_checksum=reviewer_output.reviewed_primary_output_checksum,
        reviewer_output_checksum=reviewer_checksum,
        review_dimensions=reviewer_output.review_dimensions,
    )
    reviewer_path = output_dir / "reviewer_llm_output.json"
    _write_json(reviewer_path, asdict(reviewer_output))

    final_artifact = _build_final_artifact(
        state=state,
        phase=phase,
        stage_index=stage_index,
        deterministic_ref=primary_input.deterministic_artifact_ref,
        deterministic_checksum=deterministic_checksum,
        primary_output=primary_output,
        primary_checksum=primary_checksum,
        reviewer_output=reviewer_output,
        reviewer_checksum=reviewer_checksum,
        artifact_refs=artifact_refs,
    )
    final_checksum = compute_final_markdown_checksum(final_artifact)
    final_artifact = _with_final_checksum(final_artifact, final_checksum)
    final_failures = validate_final_markdown(final_artifact)
    if final_failures:
        raise ReviewChainProductionError(
            "invalid final reviewed markdown: " + "; ".join(final_failures)
        )
    final_path = output_dir / "final_reviewed_markdown.md"
    final_path.write_text(_render_final_markdown(final_artifact), encoding="utf-8")

    chain = CompleteChecksumChain(
        deterministic_artifact_checksum=deterministic_checksum,
        primary_input_checksum=primary_input_checksum,
        primary_output_checksum=primary_checksum,
        reviewer_input_checksum=reviewer_input_checksum,
        reviewer_output_checksum=reviewer_checksum,
        final_markdown_checksum=final_checksum,
        job_id=job_id,
        phase=phase,
        stage_index=stage_index,
        source_profile=_profile_text(state, "source_profile"),
        target_profile=_profile_text(state, "target_profile"),
        review_decision=ReviewerDecision.ACCEPT.value,
        review_confidence=reviewer_output.confidence,
        artifact_ref=str(final_path),
    )
    chain_failures = validate_complete_checksum_chain(chain)
    if chain_failures:
        raise ReviewChainProductionError(
            "invalid checksum chain: " + "; ".join(chain_failures)
        )

    review_chain = {
        **safe_metadata_dict(chain),
        "deterministic_artifact_ref": str(deterministic_path),
        "primary_output_ref": str(primary_path),
        "reviewer_output_ref": str(reviewer_path),
        "final_markdown_ref": str(final_path),
        "reviewer_decision": ReviewerDecision.ACCEPT.value,
        "reviewed_artifact_checksum": deterministic_checksum,
        "reviewed_primary_output_checksum": primary_checksum,
        "reviewer_notes": list(reviewer_output.notes),
    }
    review_chain_path = output_dir / "review_chain.json"
    _write_json(review_chain_path, review_chain)

    produced_refs = {
        "deterministic_artifact": str(deterministic_path),
        "primary_llm_output": str(primary_path),
        "reviewer_llm_output": str(reviewer_path),
        "final_reviewed_markdown": str(final_path),
        "review_chain_metadata": str(review_chain_path),
    }
    candidate_result = {
        "job_id": job_id,
        "artifact_refs": produced_refs,
        "review_chain": review_chain,
    }
    runtime_failures = validate_runtime_review_chain_result(
        candidate_result,
        phase=phase,
        stage_index=stage_index,
        expected_job_id=job_id,
    )
    if runtime_failures:
        raise ReviewChainProductionError(
            "runtime review-chain validation failed: " + "; ".join(runtime_failures)
        )

    return {"artifact_refs": produced_refs, "review_chain": review_chain}


def _choose_deterministic_ref(phase: str, artifact_refs: dict[str, str]) -> str:
    if phase == "analysis":
        for key in ("analysis_report", "analysis_summary", "source_profile_detection"):
            if artifact_refs.get(key):
                return str(artifact_refs[key])
    for key in ("migration_plan.yaml", "migration_units.yaml", "plan_summary.md", "target_dependency_plan"):
        if artifact_refs.get(key):
            return str(artifact_refs[key])
    for value in artifact_refs.values():
        if value:
            return str(value)
    return ""


def _build_deterministic_payload(
    *,
    state: MigrationState,
    phase: str,
    stage_index: int,
    artifact_refs: dict[str, str],
    deterministic_facts: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "2.0.0",
        "job_id": str(state.get("job_id") or state.get("run_id") or ""),
        "run_id": str(state.get("run_id") or ""),
        "phase": phase,
        "stage_index": stage_index,
        "created_at": utc_now_text(),
        "artifact_refs": dict(sorted((str(k), str(v)) for k, v in artifact_refs.items() if v)),
        "deterministic_facts": _jsonable(deterministic_facts),
        "source_profile": _profile_text(state, "source_profile"),
        "target_profile": _profile_text(state, "target_profile"),
    }


def _coerce_primary_output(
    *,
    content: str,
    phase: str,
    warnings: list[str],
) -> PrimaryLLMOutput:
    parsed = _parse_json_object(content)
    if parsed is None:
        parsed = {
            "reasoning": str(content).strip(),
            "draft_markdown": str(content).strip(),
            "risks": warnings or ["No additional model risks supplied."],
            "confidence": 0.7,
            "recommended_next_step": f"Open {phase} review checkpoint for human decision.",
        }
    risks = parsed.get("risks") if isinstance(parsed.get("risks"), list) else warnings
    return PrimaryLLMOutput(
        reasoning=str(parsed.get("reasoning") or parsed.get("summary") or "").strip(),
        risks=tuple(str(r) for r in (risks or ["No additional model risks supplied."]) if str(r).strip()),
        confidence=float(parsed.get("confidence", 0.7)),
        recommended_next_step=str(
            parsed.get("recommended_next_step")
            or f"Open {phase} review checkpoint for human decision."
        ).strip(),
        draft_markdown=str(parsed.get("draft_markdown") or parsed.get("markdown") or content).strip(),
        machine_readable_metadata={
            "phase": phase,
            "model_output_format": "json" if isinstance(parsed, dict) else "text",
        },
    )


def _coerce_reviewer_output(
    *,
    content: str,
    deterministic_checksum: str,
    primary_checksum: str,
) -> ReviewerLLMOutput:
    parsed = _parse_json_object(content)
    if parsed is None:
        raise ReviewChainProductionError("reviewer output must be JSON")
    raw_decision = str(parsed.get("decision") or "").strip().lower()
    decision = "request_revision" if raw_decision == "revise" else raw_decision
    notes = parsed.get("notes")
    if not isinstance(notes, list):
        notes = [parsed.get("reasoning") or parsed.get("summary") or "Reviewer accepted the output."]
    risks = parsed.get("risks") if isinstance(parsed.get("risks"), list) else []
    concerns = (
        parsed.get("policy_concerns")
        if isinstance(parsed.get("policy_concerns"), list)
        else parsed.get("unsafe_assumptions")
        if isinstance(parsed.get("unsafe_assumptions"), list)
        else []
    )
    return ReviewerLLMOutput(
        decision=decision,
        notes=tuple(str(note) for note in notes if str(note).strip()),
        confidence=float(parsed.get("confidence", 0.8)),
        risks=tuple(str(risk) for risk in risks if str(risk).strip()),
        policy_concerns=tuple(str(item) for item in concerns if str(item).strip()),
        reviewed_artifact_checksum=str(
            parsed.get("reviewed_artifact_checksum") or deterministic_checksum
        ),
        reviewed_primary_output_checksum=str(
            parsed.get("reviewed_primary_output_checksum") or primary_checksum
        ),
        review_dimensions=dict(parsed.get("review_dimensions") or {}),
    )


def _build_final_artifact(
    *,
    state: MigrationState,
    phase: str,
    stage_index: int,
    deterministic_ref: str,
    deterministic_checksum: str,
    primary_output: PrimaryLLMOutput,
    primary_checksum: str,
    reviewer_output: ReviewerLLMOutput,
    reviewer_checksum: str,
    artifact_refs: dict[str, str],
) -> FinalReviewedMarkdown:
    metadata = FinalMarkdownMetadata(
        job_id=str(state.get("job_id") or state.get("run_id") or ""),
        phase=phase,
        stage_index=stage_index,
        source_profile=_profile_text(state, "source_profile"),
        target_profile=_profile_text(state, "target_profile"),
        deterministic_artifact_ref=deterministic_ref,
        deterministic_artifact_checksum=deterministic_checksum,
        primary_output_checksum=primary_checksum,
        reviewer_output_checksum=reviewer_checksum,
        review_decision=reviewer_output.decision,
        review_confidence=reviewer_output.confidence,
        created_at=utc_now_text(),
    )
    safe_refs = tuple(str(v) for _, v in sorted(artifact_refs.items()) if v)
    return FinalReviewedMarkdown(
        summary=f"{phase.title()} reviewed artifact accepted by reviewer model.",
        inputs_used=f"Deterministic {phase} artifact: {deterministic_ref}",
        deterministic_findings=_compact_json(_jsonable(artifact_refs)),
        file_names=tuple(Path(ref).name for ref in safe_refs if Path(ref).name),
        primary_reasoning=primary_output.reasoning,
        reviewer_notes="\n".join(reviewer_output.notes),
        risks=primary_output.risks or reviewer_output.risks or ("No additional risks supplied.",),
        confidence=min(primary_output.confidence, reviewer_output.confidence),
        recommended_next_step=primary_output.recommended_next_step,
        metadata=metadata,
        safe_artifact_refs=safe_refs,
    )


def _with_final_checksum(
    artifact: FinalReviewedMarkdown,
    final_checksum: str,
) -> FinalReviewedMarkdown:
    metadata = artifact.metadata
    return FinalReviewedMarkdown(
        summary=artifact.summary,
        inputs_used=artifact.inputs_used,
        deterministic_findings=artifact.deterministic_findings,
        file_names=artifact.file_names,
        primary_reasoning=artifact.primary_reasoning,
        reviewer_notes=artifact.reviewer_notes,
        risks=artifact.risks,
        confidence=artifact.confidence,
        recommended_next_step=artifact.recommended_next_step,
        metadata=FinalMarkdownMetadata(
            job_id=metadata.job_id,
            phase=metadata.phase,
            stage_index=metadata.stage_index,
            source_profile=metadata.source_profile,
            target_profile=metadata.target_profile,
            deterministic_artifact_ref=metadata.deterministic_artifact_ref,
            deterministic_artifact_checksum=metadata.deterministic_artifact_checksum,
            primary_output_checksum=metadata.primary_output_checksum,
            reviewer_output_checksum=metadata.reviewer_output_checksum,
            review_decision=metadata.review_decision,
            review_confidence=metadata.review_confidence,
            final_markdown_checksum=final_checksum,
            created_at=metadata.created_at,
            schema_version=metadata.schema_version,
        ),
        safe_artifact_refs=artifact.safe_artifact_refs,
        markdown_body=artifact.markdown_body,
    )


def _render_final_markdown(artifact: FinalReviewedMarkdown) -> str:
    metadata = asdict(artifact.metadata)
    return "\n".join(
        [
            f"# {artifact.metadata.phase.title()} Reviewed Artifact",
            "",
            "## Summary",
            artifact.summary,
            "",
            "## Inputs Used",
            artifact.inputs_used,
            "",
            "## Deterministic Findings",
            artifact.deterministic_findings,
            "",
            "## File Names And File Paths",
            "\n".join(f"- {ref}" for ref in artifact.safe_artifact_refs),
            "",
            "## Primary LLM Reasoning",
            artifact.primary_reasoning,
            "",
            "## Reviewer LLM Notes",
            artifact.reviewer_notes,
            "",
            "## Risks",
            "\n".join(f"- {risk}" for risk in artifact.risks),
            "",
            "## Confidence",
            str(artifact.confidence),
            "",
            "## Recommended Next Step",
            artifact.recommended_next_step,
            "",
            "## Machine-Readable Metadata",
            "```json",
            json.dumps(metadata, sort_keys=True, indent=2),
            "```",
            "",
        ]
    )


def _primary_prompt(input_: PrimaryLLMInput) -> str:
    return (
        "Create a reviewed-checkpoint draft Markdown artifact from the deterministic "
        "migration evidence. Return JSON with keys reasoning, risks, confidence, "
        "recommended_next_step, draft_markdown. Do not include commands, paths to execute, "
        "provider data, endpoint data, env data, or approvals.\n\n"
        f"Input:\n{json.dumps(asdict(input_), sort_keys=True)}"
    )


def _reviewer_prompt(input_: ReviewerLLMInput) -> str:
    return (
        "Review another model's migration artifact draft. Return JSON with decision "
        "(accept, reject, or request_revision), notes, confidence, risks, policy_concerns, "
        "reviewed_artifact_checksum, reviewed_primary_output_checksum, and review_dimensions. "
        "Accept only if the draft is grounded in the exact checksums and contains no unsafe "
        "execution authority.\n\n"
        f"Input:\n{json.dumps(asdict(input_), sort_keys=True)}"
    )


def _safe_preview(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, default=str)[:4000]


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(_jsonable(payload), sort_keys=True, indent=2),
        encoding="utf-8",
    )


def _parse_json_object(content: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(str(content))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _profile_text(state: MigrationState, key: str) -> str | None:
    value = state.get(key)
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        profile_id = value.get("id") or value.get("profile_id")
        return str(profile_id) if profile_id else None
    return None


def _profile_dict(state: MigrationState, key: str) -> dict[str, Any] | None:
    value = state.get(key)
    if isinstance(value, dict):
        return _jsonable(value)
    if isinstance(value, str) and value:
        return {"id": value}
    return None


def _compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
