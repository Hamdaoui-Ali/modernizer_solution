import json
from pathlib import Path

import pytest

from migration_factory.orchestrator import approval as approval_module
from migration_factory.orchestrator.approval import (
    approval_node,
    build_approval_payload,
)
from migration_factory.orchestrator.state import build_initial_state


def _state(tmp_path: Path):
    state = build_initial_state(
        run_id="run-001",
        legacy_app_path=str(tmp_path / "legacy"),
        modernized_app_path=str(tmp_path / "modernized"),
    )
    state["analysis_status"] = "PASS"
    state["planning_status"] = "PASS"
    state["assessment_status"] = "PASS"
    state["orchestration_status"] = "PASS"
    state["artifact_refs"] = {"assessment": "assessment/report.json"}
    state["blockers"] = ["needs owner review"]
    state["warnings"] = ["manual approval required"]
    return state


def test_build_approval_payload_is_json_safe(tmp_path: Path) -> None:
    payload = build_approval_payload(_state(tmp_path))

    assert payload["type"] == "human_approval_required"
    assert payload["run_id"] == "run-001"
    assert payload["summary"]["assessment_status"] == "PASS"
    assert payload["artifact_refs"] == {"assessment": "assessment/report.json"}
    assert payload["blockers"] == ["needs owner review"]
    assert payload["warnings"] == ["manual approval required"]
    assert payload["decision_options"] == [
        "approved",
        "rejected",
        "replan_required",
    ]
    json.dumps(payload)


def test_approval_node_emits_interrupt_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    emitted = []

    def fake_interrupt(payload):
        emitted.append(payload)
        return {"decision": "approved"}

    monkeypatch.setattr(approval_module, "interrupt", fake_interrupt)

    result = approval_node(_state(tmp_path))

    assert emitted == [build_approval_payload(_state(tmp_path))]
    assert result["approval_status"] == "COMPLETED"
    assert result["approval_decision"] == "approved"


@pytest.mark.parametrize("decision", ["approved", "rejected", "replan_required"])
def test_approval_node_accepts_valid_decisions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    decision: str,
) -> None:
    monkeypatch.setattr(
        approval_module,
        "interrupt",
        lambda payload: {"decision": decision},
    )

    result = approval_node(_state(tmp_path))

    assert result == {
        "approval_status": "COMPLETED",
        "approval_decision": decision,
        "current_phase": "approval",
        "stop_reason": f"Approval decision '{decision}' received; stopping.",
    }


@pytest.mark.parametrize("resume_payload", [{}, {"decision": "bad"}, "approved"])
def test_approval_node_rejects_invalid_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    resume_payload,
) -> None:
    monkeypatch.setattr(
        approval_module,
        "interrupt",
        lambda payload: resume_payload,
    )

    result = approval_node(_state(tmp_path))

    assert result["approval_status"] == "FAILED"
    assert result["approval_decision"] is None
    assert result["current_phase"] == "approval"
    assert result["stop_reason"].startswith("Invalid approval decision:")
    assert result["blockers"][-1] == result["stop_reason"]
    assert result["errors"][-1] == result["stop_reason"]


def test_invalid_approval_decision_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        approval_module,
        "interrupt",
        lambda payload: {"decision": "transform_now"},
    )

    result = approval_node(_state(tmp_path))

    assert result["approval_status"] == "FAILED"
    assert result["approval_decision"] is None
    assert result["stop_reason"] == "Invalid approval decision: 'transform_now'"
    assert result["blockers"][-1] == result["stop_reason"]


def test_approval_payload_has_no_transformation_keys(tmp_path: Path) -> None:
    payload = build_approval_payload(_state(tmp_path))
    payload_text = json.dumps(payload).lower()

    assert "transformation" not in payload_text
    assert "transformation_key" not in payload
    assert "transformation_route" not in payload
    assert "transformation_status" not in payload
