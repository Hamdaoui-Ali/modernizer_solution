from dataclasses import dataclass

from migration_factory.contracts.planning_assist import PlanningAssistRequest, PlanningAssistResult


@dataclass(frozen=True)
class AssistOutputValidationResult:
    sanitized_result: PlanningAssistResult
    warnings: list[str]
    errors: list[str]


def validate_assist_output_for_merge(
    *, request: PlanningAssistRequest, assist_result: PlanningAssistResult
) -> AssistOutputValidationResult:
    warnings: list[str] = []
    errors: list[str] = []

    if assist_result.status in {"SKIPPED", "UNAVAILABLE", "ERROR"}:
        return AssistOutputValidationResult(
            sanitized_result=assist_result,
            warnings=warnings,
            errors=errors,
        )

    if assist_result.status != "USED":
        errors.append("Planning assist invalid output status.")

    if assist_result.confidence is not None and not (0.0 <= assist_result.confidence <= 1.0):
        errors.append("Planning assist confidence must be within [0, 1].")

    # Advisory-only contract: only fields already used by merge are accepted.
    forbidden_payload_keys = set(request.forbidden_fields)

    def _collect_structural_attempts(values: list[str], field_name: str) -> None:
        lowered_keys = [key.lower() for key in forbidden_payload_keys]
        for value in values:
            lowered = value.lower()
            if any(key in lowered for key in lowered_keys):
                warnings.append(
                    f"[WARNING] Assist attempted forbidden structural field change in {field_name}."
                )

    _collect_structural_attempts(assist_result.missing_warnings, "missing_warnings")
    _collect_structural_attempts(assist_result.warnings, "warnings")
    _collect_structural_attempts(
        assist_result.approval_summary_improvements,
        "approval_summary_improvements",
    )
    _collect_structural_attempts(assist_result.operator_notes, "operator_notes")
    _collect_structural_attempts(assist_result.risk_explanations, "risk_explanations")

    if errors:
        reason = "; ".join(errors)
        return AssistOutputValidationResult(
            sanitized_result=PlanningAssistResult(
                status="ERROR",
                warnings=[
                    "[WARNING] Planning assist failed-open: "
                    f"{reason}"
                ],
                error=reason,
            ),
            warnings=warnings,
            errors=errors,
        )

    return AssistOutputValidationResult(
        sanitized_result=PlanningAssistResult(
            status="USED",
            missing_warnings=list(assist_result.missing_warnings),
            approval_summary_improvements=list(assist_result.approval_summary_improvements),
            operator_notes=list(assist_result.operator_notes),
            risk_explanations=list(assist_result.risk_explanations),
            confidence=assist_result.confidence,
            warnings=list(assist_result.warnings),
            error=assist_result.error,
        ),
        warnings=warnings,
        errors=errors,
    )
