from dataclasses import fields
from typing import get_args

from migration_factory.contracts import (
    AssistResultStatus,
    PlanningAssistRequest,
    PlanningAssistResult,
)


def test_planning_assist_request_contract_fields() -> None:
    field_names = [field.name for field in fields(PlanningAssistRequest)]

    assert field_names == [
        "run_id",
        "agent",
        "phase",
        "model",
        "prompt",
        "context",
        "allowed_fields",
        "forbidden_fields",
    ]


def test_assist_result_status_values() -> None:
    assert set(get_args(AssistResultStatus)) == {"USED", "SKIPPED", "UNAVAILABLE", "ERROR"}


def test_planning_assist_result_advisory_fields() -> None:
    field_names = [field.name for field in fields(PlanningAssistResult)]

    assert field_names == [
        "status",
        "missing_warnings",
        "approval_summary_improvements",
        "operator_notes",
        "risk_explanations",
        "confidence",
        "warnings",
        "error",
        "requested_model",
        "resolved_model",
        "model_source",
        "model_verified",
    ]


def test_contracts_package_exports_assist_contracts() -> None:
    request = PlanningAssistRequest(
        run_id="r-1",
        agent="planning-agent",
        phase="planning",
        model="gpt-test",
        prompt="review",
        context={"migration_units": []},
    )
    result = PlanningAssistResult(status="SKIPPED")

    assert request.run_id == "r-1"
    assert result.status == "SKIPPED"
