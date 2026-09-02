from migration_factory.agents.planning_agent.assist_output_validator import (
    validate_assist_output_for_merge,
)
from migration_factory.contracts.planning_assist import (
    PlanningAssistRequest,
    PlanningAssistResult,
)


def _request() -> PlanningAssistRequest:
    return PlanningAssistRequest(
        run_id="r1",
        agent="planning-agent",
        phase="planning",
        model="gpt-test",
        prompt="review plan",
        context={"migration_units": []},
        allowed_fields=["warnings", "approval_summary", "operator_notes", "risks"],
        forbidden_fields=[
            "unit_order",
            "tools",
            "blockers",
            "approval_required",
            "executable",
        ],
    )


def test_rejects_confidence_outside_zero_to_one() -> None:
    result = validate_assist_output_for_merge(
        request=_request(),
        assist_result=PlanningAssistResult(status="USED", confidence=1.5),
    )
    assert result.sanitized_result.status == "ERROR"
    assert result.sanitized_result.error == "Planning assist confidence must be within [0, 1]."


def test_detects_forbidden_structural_change_attempts_as_warnings() -> None:
    result = validate_assist_output_for_merge(
        request=_request(),
        assist_result=PlanningAssistResult(
            status="USED",
            warnings=["attempt tools=['copilot']"],
            operator_notes=["set approval_required=false"],
        ),
    )
    assert result.sanitized_result.status == "USED"
    assert result.warnings
