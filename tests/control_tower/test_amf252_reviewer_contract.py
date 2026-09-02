"""AMF-252 Reviewer schema/alias regression coverage (not run in investigation)."""

from __future__ import annotations

import json

import pytest

from migration_factory.control_tower.application.v2_model_schemas import validate_model_output
from migration_factory.control_tower.application.v2_model_role_router import (
    V2ModelRole,
    V2ModelRoleRouter,
    V2RoleModelRequest,
)


def _reviewer_payload() -> dict:
    return {
        "decision": "accept", "proposed_diff": "", "proposed_edits": [], "changed_files": [],
        "review_notes": ["ok"], "risks": [], "confidence": 1.0, "policy_concerns": [],
        "reviewed_context_checksum": "context", "reviewed_primary_output_checksum": "primary",
        "reviewed_diff_checksum": "diff",
    }


def test_reviewer_notes_alias_is_normalized_without_contract_weakening() -> None:
    payload = _reviewer_payload()
    payload.pop("notes", None)
    normalized = validate_model_output("RepairReviewerOutput", payload)
    assert normalized["notes"] == normalized["review_notes"]


def test_reviewer_malformed_payload_fails_closed() -> None:
    payload = _reviewer_payload()
    payload.pop("decision")
    with pytest.raises(Exception):
        validate_model_output("RepairReviewerOutput", payload)


def test_reviewer_primary_failure_fallback_success(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_REVIEWER_DEPLOYMENT", "reviewer")
    monkeypatch.setenv("AZURE_OPENAI_FALLBACK_DEPLOYMENT", "fallback")
    monkeypatch.setenv("CONTROL_TOWER_AZURE_FOUNDRY_FALLBACK_ENABLED", "true")
    router = V2ModelRoleRouter()
    request = V2RoleModelRequest(
        role=V2ModelRole.REVIEWER, prompt="review", fallback="fallback",
        output_schema_name="RepairReviewerOutput", require_schema=True,
    )
    calls = []

    def invoke(deployment: str):
        calls.append(deployment)
        if len(calls) == 1:
            return type("Failure", (), {"content": "{}", "success": True, "failure_reason": ""})()
        return type("Success", (), {"content": json.dumps(_reviewer_payload()), "success": True, "failure_reason": ""})()

    result = router.route(request, invoke=invoke)
    assert result.success is True
    assert result.fallback_used is True
    assert len(calls) == 2
