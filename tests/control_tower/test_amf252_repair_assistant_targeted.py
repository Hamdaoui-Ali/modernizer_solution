"""AMF-252 targeted Assistant contract tests.

These tests are intentionally not executed by the AMF-252 investigation run.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from types import SimpleNamespace
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from migration_factory.control_tower.adapters.fastapi import create_app
from migration_factory.control_tower.application.repair_assistant_service import RepairAssistantService
from migration_factory.control_tower.application.v2_model_role_router import V2ModelRole
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.repair_assistant_repository import SqliteRepairAssistantRepository
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteControlTowerUnitOfWork
from migration_factory.control_tower.infrastructure.sqlite.v2_repair_repository import (
    SqliteV2RepairRepository,
    V2RepairProposalRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import (
    SqliteV2JobRepository,
    V2MigrationJobRecord,
)


class _DeterministicAssistant:
    def __init__(self, action: str = "ANSWER_ONLY") -> None:
        self.action = action
        self.calls = 0

    def answer_with_role(self, *, role, prompt, fallback, **kwargs):
        assert role is V2ModelRole.ASSISTANT
        self.calls += 1
        return type("Result", (), {
            "content": json.dumps({
                "action": self.action,
                "assistant_message": "Deterministic response",
                "revision_instruction": "Regenerate proposal with requested correction" if self.action == "REQUEST_REVISION" else "",
                "constraints": [], "target_files": [], "target_coordinates": [],
                "requires_clarification": False,
            }),
            "success": True, "failure_reason": "", "redacted_summary": "deterministic",
            "primary_http_status": "", "fallback_http_status": "", "response_format_used": "json_object",
        })()


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tmp_path / "amf252.sqlite3"), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    return conn


def _proposal(conn: sqlite3.Connection, tmp_path: Path, *, job_id: str = "job-a", proposal_id: str = "proposal-a") -> str:
    diff = tmp_path / "proposal.diff"
    diff.write_text("diff --git a/pom.xml b/pom.xml\n", encoding="utf-8")
    checksum = hashlib.sha256(diff.read_bytes()).hexdigest()
    SqliteV2RepairRepository(conn).save_proposal(V2RepairProposalRecord(
        proposal_id=proposal_id, command_id="command-a", failure_summary="build failed",
        hypothesis="bad dependency", patch_summary="remove dependency", affected_paths_json='["pom.xml"]',
        status="user_review_required", approval_checksum=None, created_at=utc_now_text(), job_id=job_id,
        attempt_number=1, diff_ref=str(diff), diff_checksum=checksum,
    ))
    SqliteV2JobRepository(conn).save(V2MigrationJobRecord(
        job_id=job_id, setup_id="setup-a", setup_checksum="setup", pipeline_id="pipeline-a",
        stage_chain_json="[]", status="created", created_at=utc_now_text(), updated_at=utc_now_text(),
        correlation_id=None,
    ))
    return checksum


def test_assistant_get_empty_messages_uses_real_uow(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    proposal_checksum = _proposal(conn, tmp_path)
    app = create_app(lambda: SqliteControlTowerUnitOfWork(conn))
    response = TestClient(app).get("/v1/v2/jobs/job-a/repair/proposals/proposal-a/assistant/messages", headers={"Host": "127.0.0.1:8000"})
    assert response.status_code == 200
    assert response.json() == {"messages": [], "job_id": "job-a", "proposal_id": "proposal-a"}
    assert proposal_checksum


def test_assistant_get_post_get_returns_serialized_ordered_history(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    checksum = _proposal(conn, tmp_path)
    fake = _DeterministicAssistant()
    app = create_app(
        lambda: SqliteControlTowerUnitOfWork(conn),
        v2_assistant_model_client=fake,
    )
    client = TestClient(app)
    path = "/v1/v2/jobs/job-a/repair/proposals/proposal-a/assistant/messages"
    headers = {
        "Host": "127.0.0.1:8000",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": "control-tower-frontend",
    }

    assert client.get(path, headers=headers).json()["messages"] == []
    post = client.post(
        path,
        headers=headers,
        json={
            "message": "Explain failure",
            "idempotency_key": "history-idempotency",
            "base_diff_checksum": checksum,
        },
    )
    assert post.status_code == 200

    history_response = client.get(path, headers=headers)
    assert history_response.status_code == 200
    history = history_response.json()["messages"]
    assert len(history) == 2
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert history[0]["message"] == "Explain failure"
    assert history[0]["action"] is None
    assert history[1]["message"] == "Deterministic response"
    assert history[1]["action"] == "ANSWER_ONLY"
    assert all(item["job_id"] == "job-a" and item["proposal_id"] == "proposal-a" for item in history)
    assert all(item["message_id"] and item["status"] and item["created_at"] for item in history)
    assert [row.role for row in SqliteRepairAssistantRepository(conn).list_messages("job-a", "proposal-a")] == ["user", "assistant"]


def test_assistant_post_persists_user_and_exact_response_and_calls_once(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    checksum = _proposal(conn, tmp_path)
    fake = _DeterministicAssistant()
    service = RepairAssistantService(
        repair_assistant_repo=SqliteRepairAssistantRepository(conn),
        repair_repo=SqliteV2RepairRepository(conn),
        model_client=fake,
    )
    result = service.process_message(
        job_id="job-a", proposal_id="proposal-a", message="Explain failure",
        idempotency_key="idempotency-a", base_diff_checksum=checksum,
    )
    assert result.status == "answered"
    assert fake.calls == 1
    rows = SqliteRepairAssistantRepository(conn).list_messages("job-a", "proposal-a")
    assert [row.role for row in rows] == ["user", "assistant"]
    assert rows[-1].message_text == "Deterministic response"


def test_assistant_context_separates_unapplied_proposal_from_apply_validation(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    checksum = _proposal(conn, tmp_path)
    service = RepairAssistantService(
        repair_assistant_repo=SqliteRepairAssistantRepository(conn),
        repair_repo=SqliteV2RepairRepository(conn),
        model_client=_DeterministicAssistant(),
    )
    context = service.build_repair_assistant_context(job_id="job-a", proposal_id="proposal-a")
    prompt = service._build_model_prompt(context)
    assert "=== ORIGINAL_FAILURE_EVIDENCE ===" in prompt
    assert "=== CURRENT_UNAPPLIED_PROPOSAL ===" in prompt
    assert "=== LATEST_APPLY_VALIDATION_RESULT ===\nNONE" in prompt
    assert "=== PREVIOUS VALIDATION RESULT ===" not in prompt
    assert checksum


def test_request_revision_is_persisted_intent_without_auto_apply(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    checksum = _proposal(conn, tmp_path)
    fake = _DeterministicAssistant("REQUEST_REVISION")
    result = RepairAssistantService(
        repair_assistant_repo=SqliteRepairAssistantRepository(conn),
        repair_repo=SqliteV2RepairRepository(conn), model_client=fake,
    ).process_message(
        job_id="job-a", proposal_id="proposal-a", message="Fix it",
        idempotency_key="idempotency-revision", base_diff_checksum=checksum,
    )
    assert result.action == "REQUEST_REVISION"
    assert result.revision_started is False
    assert result.new_proposal_id is None
    assert fake.calls == 1


def test_assistant_revision_api_creates_one_new_proposal_and_replays_idempotently(
    tmp_path: Path, monkeypatch,
) -> None:
    conn = _db(tmp_path)
    checksum = _proposal(conn, tmp_path)
    fake = _DeterministicAssistant("REQUEST_REVISION")
    new_diff = tmp_path / "proposal-2.diff"
    new_diff.write_text(
        "+ <juneau.version>8.1.4</juneau.version>\n", encoding="utf-8",
    )
    new_checksum = hashlib.sha256(new_diff.read_bytes()).hexdigest()
    new_id = "proposal-b"

    def fake_revision(**kwargs):
        with SqliteControlTowerUnitOfWork(conn) as uow:
            uow.transaction_mode = "write"
            uow.v2_repairs.update_proposal_status(kwargs["proposal"].proposal_id, "superseded")
            uow.v2_repairs.save_proposal(V2RepairProposalRecord(
                proposal_id=new_id, command_id="command-a", failure_summary="revised",
                hypothesis="version", patch_summary="juneau 8.1.4", affected_paths_json='["pom.xml"]',
                status="user_review_required", approval_checksum=None, created_at=utc_now_text(),
                job_id="job-a", attempt_number=2, diff_ref=str(new_diff), diff_checksum=new_checksum,
                revision_of=kwargs["proposal"].proposal_id, revision_number=1,
            ))
        return SimpleNamespace(status="created", proposal_id=new_id, attempt_number=2, diff_checksum=new_checksum)

    import migration_factory.control_tower.adapters.fastapi.app as app_module
    monkeypatch.setattr(app_module, "_create_direct_repair_revision", fake_revision)
    app = create_app(lambda: SqliteControlTowerUnitOfWork(conn), v2_assistant_model_client=fake)
    client = TestClient(app)
    path = "/v1/v2/jobs/job-a/repair/proposals/proposal-a/assistant/messages"
    headers = {"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:3000", "X-Control-Tower-Client": "control-tower-frontend"}
    payload = {
        "message": "Set juneau.version to 8.1.4 and update the diff.",
        "idempotency_key": "revision-idempotency",
        "base_diff_checksum": checksum,
    }
    response = client.post(path, headers=headers, json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action"] == "REQUEST_REVISION"
    assert body["revision_started"] is True
    assert body["new_proposal_id"] == new_id
    assert body["new_attempt_number"] == 2
    assert body["new_diff_checksum"] == new_checksum

    messages = SqliteRepairAssistantRepository(conn).list_messages("job-a", "proposal-a")
    assert json.loads(messages[-1].revision_intent_json)["revision_instruction"] == payload["message"]
    assert len(SqliteV2RepairRepository(conn).list_proposals_by_job("job-a")) == 2
    assert SqliteV2RepairRepository(conn).get_proposal_for_job("job-a", "proposal-a").status == "superseded"

    retry = client.post(path, headers=headers, json=payload)
    assert retry.status_code == 200
    assert retry.json()["new_proposal_id"] == new_id
    assert len(SqliteV2RepairRepository(conn).list_proposals_by_job("job-a")) == 2


@pytest.mark.parametrize("message", [
    "Set juneau.version to 8.1.4",
    "Modify src/main/java/example/App.java",
    "Adjust the Angular configuration",
    "Change this dependency",
])
def test_generic_revision_intent_preserves_exact_user_instruction(message: str) -> None:
    intent = RepairAssistantService._parse_intent(json.dumps({
        "action": "REQUEST_REVISION",
        "assistant_message": "Revision requested.",
        "tool": "request_repair_revision",
        "arguments": {
            "user_instruction": "model hint that must not win",
            "resolved_instruction": "optional hint",
            "constraints": [],
            "target_files": [],
        },
        "requires_clarification": False,
    }), user_message=message)
    assert intent is not None
    assert intent.tool == "request_repair_revision"
    assert intent.user_instruction == message
    assert intent.target_files == []
