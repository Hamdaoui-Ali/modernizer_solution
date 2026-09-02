from dataclasses import dataclass

from migration_factory.contracts.planning_assist import PlanningAssistResult


@dataclass(frozen=True)
class AssistMergeResult:
    approval_summary: str
    warnings: list[str]
    operator_notes: list[str]
    risk_explanations: list[str]


def merge_advisory_assist_suggestions(
    *,
    deterministic_approval_summary: str,
    deterministic_warnings: list[str],
    assist_result: PlanningAssistResult,
) -> AssistMergeResult:
    merged_summary = deterministic_approval_summary
    merged_warnings = [*deterministic_warnings]
    operator_notes: list[str] = []
    risk_explanations: list[str] = []

    if assist_result.status != "USED":
        return AssistMergeResult(
            approval_summary=merged_summary,
            warnings=merged_warnings,
            operator_notes=operator_notes,
            risk_explanations=risk_explanations,
        )

    if assist_result.approval_summary_improvements:
        merged_summary = assist_result.approval_summary_improvements[-1].strip() or merged_summary

    for warning in assist_result.missing_warnings:
        text = warning.strip()
        if text:
            merged_warnings.append(f"[WARNING] {text}")

    for warning in assist_result.warnings:
        text = warning.strip()
        if text:
            merged_warnings.append(f"[INFO] {text}")

    operator_notes = [note.strip() for note in assist_result.operator_notes if note.strip()]
    risk_explanations = [
        explanation.strip()
        for explanation in assist_result.risk_explanations
        if explanation.strip()
    ]

    return AssistMergeResult(
        approval_summary=merged_summary,
        warnings=merged_warnings,
        operator_notes=operator_notes,
        risk_explanations=risk_explanations,
    )
