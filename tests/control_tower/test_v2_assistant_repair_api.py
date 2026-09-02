"""Integration tests for V2 assistant and repair API endpoints."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
import migration_factory.control_tower.application.v2_repair_flow as v2_repair_flow

from migration_factory.control_tower.application.v2_repair_flow import V2RepairFlowService
from migration_factory.control_tower.application.v2_setup_service import (
    CreateSetupRequest,
    V2SetupService,
)
from migration_factory.control_tower.application.v2_model_role_router import V2ModelRole
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_assistant_repository import (
    SqliteV2AssistantRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_repair_repository import (
    SqliteV2RepairRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import (
    SqliteV2JobRepository,
    V2MigrationJobRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
    V2StageCommandRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    V2MigrationSetupRecord,
)


def _mutation_headers():
    from migration_factory.control_tower.adapters.fastapi.security import DEFAULT_FRONTEND_CLIENT_ID
    return {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": DEFAULT_FRONTEND_CLIENT_ID,
    }


def _api_client(tmp_path: Path, *, fake_model_client: object | None = None):
    from migration_factory.control_tower.adapters.fastapi import create_app
    conn = sqlite3.connect(
        tmp_path / "assistant_repair_test.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_pending_migrations(conn)
    app = create_app(lambda: SqliteUnitOfWork(conn))
    if fake_model_client is not None:
        app.state.v2_assistant_model_client = fake_model_client
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    return client, conn


def _seed_repair_apply_context(
    conn: sqlite3.Connection,
    tmp_path: Path,
    *,
    proposal_id: str,
    proposal_checksum: str,
    command_id: str,
) -> Path:
    setup_repo = SqliteV2SetupRepository(conn)
    job_repo = SqliteV2JobRepository(conn)
    command_repo = SqliteV2CommandRepository(conn)
    legacy_root = Path(tmp_path / "legacy")
    sandbox_root = Path(tmp_path / "out" / ".migration" / "runs" / "run-apply-1" / "sandbox")
    legacy_root.mkdir(parents=True, exist_ok=True)
    sandbox_root.mkdir(parents=True, exist_ok=True)
    (sandbox_root / "pom.xml").write_text("<project/>", encoding="utf-8")

    setup = V2MigrationSetupRecord(
        setup_id="setup-apply-1",
        run_name="repair-apply",
        legacy_app_path=str(tmp_path / "legacy"),
        output_parent_path=str(tmp_path / "out"),
        ai_hub_path=str(tmp_path / "ai-hub"),
        java11_home="C:/java11",
        java17_home="C:/java17",
        java21_home="C:/java21",
        maven_cmd="mvn",
        proof_level="build_test_verified",
        skip_endpoint_smoke=False,
        migration_flags_json="{}",
        setup_checksum="setup-chk",
        checksum_algorithm="sha256",
        created_at="2026-06-18T00:00:00Z",
        created_by="test",
        correlation_id=None,
    )
    setup_repo.save(setup)
    job_repo.save(
        V2MigrationJobRecord(
            job_id="job-2",
            setup_id=setup.setup_id,
            setup_checksum="setup-chk",
            pipeline_id="pipeline-1",
            stage_chain_json="[]",
            status="created",
            created_at="2026-06-18T00:00:00Z",
            updated_at="2026-06-18T00:00:00Z",
            correlation_id=None,
        )
    )

    run_id = "run-apply-1"
    run_dir = Path(tmp_path / "out" / ".migration" / "runs" / run_id)
    draft_path = run_dir / "repairs" / "patch_draft_1.json"
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "proposal_id": proposal_id,
                "repair_proposal_checksum": proposal_checksum,
                "target_path": "pom.xml",
                "deterministic_rule_id": "DEPENDENCY_ADD_H2_RUNTIME",
                "risk": "LOW",
                "requires_human_review": False,
                "binding_checksum": "binding-1",
                "h2_required": True,
                "unified_diff": _h2_patch(),
                "expected_validation": ["mvn test"],
                "limitations": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    command_repo.save(
        V2StageCommandRecord(
            command_id=command_id,
            job_id="job-2",
            stage_index=3,
            manifest_checksum="manifest-chk",
            argv_json="[]",
            env_json="{}",
            status="failed",
            created_at="2026-06-18T00:00:00Z",
            updated_at="2026-06-18T00:00:00Z",
            result_json=json.dumps(
                {
                    "run_id": run_id,
                    "sandbox_path": str(run_dir / "sandbox"),
                    "modernized_app_path": str(tmp_path / "out"),
                }
            ),
            gate_id=None,
            decision_id=None,
        )
    )
    return run_dir


def _seed_repair_proposal(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    failure_summary: str = "Build failed",
    hypothesis: str = "Missing dependency",
    patch_summary: str = "Add dependency",
    affected_paths: tuple[str, ...] = ("pom.xml",),
):
    service = V2RepairFlowService(repair_repo=SqliteV2RepairRepository(conn))
    return service.create_proposal(
        command_id=command_id,
        failure_summary=failure_summary,
        hypothesis=hypothesis,
        patch_summary=patch_summary,
        affected_paths=affected_paths,
    )


def _fake_apply_result(run_dir: Path):
    from migration_factory.repair_loop.patch_apply import PatchApplyResult

    patch_path = run_dir / "repairs" / "patch_attempt_1.diff"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(_h2_patch(), encoding="utf-8")
    return PatchApplyResult(
        status="APPLIED",
        reason="ok",
        patch_path=patch_path,
        touched_paths=["pom.xml"],
        before_hashes={"pom.xml": "before"},
        after_hashes={"pom.xml": "after"},
        snapshot_dir=run_dir / "repairs" / "snapshots" / "attempt_1",
        created_paths=[],
        errors=[],
    )


def _fake_validation(passed: bool):
    from migration_factory.repair_loop.validation_runner import ValidationResult

    return ValidationResult(
        passed=passed,
        build_status="BUILD_PASSED_IN_SANDBOX" if passed else "BUILD_FAILED_IN_SANDBOX",
        test_status="TEST_PASSED" if passed else "TEST_FAILED",
        h2_status="H2_STARTUP_PASSED" if passed else "H2_STARTUP_FAILED",
        validation_commands=[["mvn", "test"]],
        artifact_refs={},
        warnings=[],
        errors=[] if passed else ["validation failed"],
    )


def _h2_patch() -> str:
    return (
        "diff --git a/pom.xml b/pom.xml\n"
        "--- a/pom.xml\n"
        "+++ b/pom.xml\n"
        "@@\n"
        " <dependencies>\n"
        "+<dependency><groupId>com.h2database</groupId><artifactId>h2</artifactId><scope>runtime</scope></dependency>\n"
    )


class _RecordingProposerClient:
    def __init__(self) -> None:
        self.roles: list[str] = []

    def answer_with_role(
        self,
        *,
        role,
        prompt: str,
        fallback: str,
        conversation_history=None,
        output_schema_name=None,
        require_schema: bool = False,
    ):
        self.roles.append(role.value)
        import json as _json

        return type("Result", (), {
            "content": _json.dumps({
                "failure_hypothesis": "Model-generated hypothesis",
                "patch_summary": "Model-generated patch summary",
                "affected_paths": ["pom.xml"],
                "validation_plan": "Run mvn -q test",
            }),
            "source": "fake",
            "model_status": "live_ok",
            "provider": "fake",
            "role": role.value,
            "success": True,
            "redacted_summary": "Fake proposer response",
            "failure_reason": "",
        })()

    def answer(self, *, prompt: str, fallback: str, conversation_history=None):
        return self.answer_with_role(
            role=V2ModelRole.PROPOSER,
            prompt=prompt,
            fallback=fallback,
            conversation_history=conversation_history,
        )


# ── Assistant API tests ────────────────────────────────────────────


class TestAssistantAPI:

    def test_add_message(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/jobs/job-1/assistant/messages",
            json={"job_id": "job-1", "role": "user", "content": "Hello"},
            headers=_mutation_headers(),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["role"] == "user"
        assert body["content"] == "Hello"

    def test_add_assistant_message(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/jobs/job-1/assistant/messages",
            json={"job_id": "job-1", "role": "assistant", "content": "Status: ready"},
            headers=_mutation_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["role"] == "assistant"

    def test_list_messages(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        # Add two messages
        client.post(
            "/v1/v2/jobs/job-1/assistant/messages",
            json={"job_id": "job-1", "role": "user", "content": "Hi"},
            headers=_mutation_headers(),
        )
        client.post(
            "/v1/v2/jobs/job-1/assistant/messages",
            json={"job_id": "job-1", "role": "assistant", "content": "Hello"},
            headers=_mutation_headers(),
        )
        response = client.get(
            "/v1/v2/jobs/job-1/assistant/messages",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert response.status_code == 200
        body = response.json()
        assert len(body["messages"]) == 2

    def test_draft_action(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/jobs/job-1/assistant/actions/draft",
            json={
                "job_id": "job-1",
                "action_type": "propose_repair",
                "reason": "Need plan for stage 1",
                "stage_index": 1,
            },
            headers=_mutation_headers(),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "draft"
        assert body["action_type"] == "propose_repair"

    def test_draft_action_persists(self, tmp_path: Path) -> None:
        """Draft should persist and be retrievable."""
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/jobs/job-1/assistant/actions/draft",
            json={
                "job_id": "job-1",
                "action_type": "explain_failure",
                "reason": "Fix NPE",
                "stage_index": 2,
            },
            headers=_mutation_headers(),
        )
        assert response.status_code == 200
        action_id = response.json()["action_id"]

        # Verify persistence
        db_path = tmp_path / "assistant_repair_test.sqlite3"
        conn2 = sqlite3.connect(
            db_path, check_same_thread=False, isolation_level=None, timeout=5.0
        )
        conn2.row_factory = sqlite3.Row
        repo = SqliteV2AssistantRepository(conn2)
        loaded = repo.get_draft(action_id)
        assert loaded is not None
        assert loaded.status == "draft"
        conn2.close()

    def test_message_persists(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/jobs/job-persist/assistant/messages",
            json={"job_id": "job-persist", "role": "user", "content": "Persist me"},
            headers=_mutation_headers(),
        )
        assert response.status_code == 200
        msg_id = response.json()["message_id"]

        db_path = tmp_path / "assistant_repair_test.sqlite3"
        conn2 = sqlite3.connect(
            db_path, check_same_thread=False, isolation_level=None, timeout=5.0
        )
        conn2.row_factory = sqlite3.Row
        repo = SqliteV2AssistantRepository(conn2)
        loaded = repo.get_message(msg_id)
        assert loaded is not None
        assert loaded.content == "Persist me"
        conn2.close()


# ── Repair API tests ───────────────────────────────────────────────


class TestRepairAPI:

    def test_create_proposal(self, tmp_path: Path) -> None:
        fake_client = _RecordingProposerClient()
        client, conn = _api_client(tmp_path, fake_model_client=fake_client)
        response = client.post(
            "/v1/v2/commands/cmd-1/repair/flow-proposal",
            json={
                "command_id": "cmd-1",
                "failure_summary": "Build failed",
                "hypothesis": "Missing import",
                "patch_summary": "Add import statement",
                "affected_paths": ["src/main.java"],
            },
            headers=_mutation_headers(),
        )
        assert response.status_code == 410, response.text
        assert response.json()["error"]["code"] == "LEGACY_REPAIR_PROPOSAL_DISABLED"
        assert fake_client.roles == []

    def test_legacy_approve_proposal_is_disabled(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        proposal = _seed_repair_proposal(
            conn,
            command_id="cmd-2",
            failure_summary="Error",
            hypothesis="Bug",
            patch_summary="Fix",
            affected_paths=("src/Fix.java",),
        )
        proposal_id = proposal.proposal_id
        proposal_checksum = proposal.proposal_checksum
        run_dir = _seed_repair_apply_context(
            conn,
            tmp_path,
            proposal_id=proposal_id,
            proposal_checksum=proposal_checksum,
            command_id="cmd-2",
        )

        # Even with a legacy reviewer critique, this route cannot authorize F5 apply.
        from migration_factory.control_tower.application.v2_reviewer_service import (
            V2ReviewerService,
        )
        from migration_factory.control_tower.infrastructure.sqlite.v2_reviewer_repository import (
            SqliteV2ReviewerRepository,
        )
        reviewer_repo = SqliteV2ReviewerRepository(conn)
        reviewer_service = V2ReviewerService(reviewer_repo=reviewer_repo)
        reviewer_service.record_critique(
            proposal_id=proposal_id,
            proposal_type="repair",
            proposal_checksum="pc-test",
            context_pack_checksum="cp-test",
            decision="accept",
            reasoning="Test critique — approved.",
            missing_evidence=(),
            unsafe_assumptions=(),
        )

        response = client.post(
            f"/v1/v2/commands/cmd-2/repair/proposal/{proposal_id}/approve",
            json={
                "approval_checksum": "chk-abc",
                "proposal_checksum": "pc-test",
                "context_pack_checksum": "cp-test",
            },
            headers=_mutation_headers(),
        )
        assert response.status_code == 410, response.text
        body = response.json()
        assert body["error"]["code"] == "LEGACY_REPAIR_APPROVAL_DISABLED"
        assert not (run_dir / "repairs" / "repair_apply_result.json").exists()

    def test_approve_missing_proposal(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/commands/cmd-3/repair/proposal/nonexistent/approve",
            json={
                "approval_checksum": "chk",
                "proposal_checksum": "pc",
                "context_pack_checksum": "cp",
            },
            headers=_mutation_headers(),
        )
        assert response.status_code == 410

    def test_proposal_persistence(self, tmp_path: Path) -> None:
        client, conn = _api_client(tmp_path)
        proposal = _seed_repair_proposal(
            conn,
            command_id="cmd-persist",
            failure_summary="Persist test",
            hypothesis="Check persistence",
            patch_summary="Verify",
            affected_paths=("test.txt",),
        )
        proposal_id = proposal.proposal_id

        # Verify in DB
        db_path = tmp_path / "assistant_repair_test.sqlite3"
        conn2 = sqlite3.connect(
            db_path, check_same_thread=False, isolation_level=None, timeout=5.0
        )
        conn2.row_factory = sqlite3.Row
        repo = SqliteV2RepairRepository(conn2)
        loaded = repo.get_proposal(proposal_id)
        assert loaded is not None
        assert loaded.failure_summary == "Persist test"
        conn2.close()


# ── Schema validation rejection tests ───────────────────────────────


class TestSchemaValidationRejection:
    """Prove that invalid model-output-like payloads are rejected at the API."""

    def test_draft_action_rejects_extra_field(self, tmp_path: Path) -> None:
        """ActionRequest schema has additionalProperties: false.

        Extra fields are rejected either by Pydantic (INVALID_REQUEST) or
        by the schema validator (SCHEMA_VALIDATION_FAILED). Both are valid
        closed-fail behaviors.
        """
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/jobs/job-1/assistant/actions/draft",
            json={
                "job_id": "job-1",
                "action_type": "diagnose_failure",
                "reason": "test",
                "stage_index": 1,
                "extra_field": "should be rejected",
            },
            headers=_mutation_headers(),
        )
        assert response.status_code == 422, f"Expected 422 for extra field, got {response.status_code}"
        body = response.json()
        err = str(body).lower()
        assert any(term in err for term in [
            "invalid_request",
            "schema_validation_failed",
            "unexpected property",
            "did not match",
        ]), f"Expected rejection message, got {body}"

    def test_draft_action_rejects_invalid_stage_index(self, tmp_path: Path) -> None:
        """ActionRequest stage_index must be 1-3."""
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/jobs/job-1/assistant/actions/draft",
            json={
                "job_id": "job-1",
                "action_type": "diagnose_failure",
                "reason": "test",
                "stage_index": 99,
            },
            headers=_mutation_headers(),
        )
        assert response.status_code == 422, f"Expected 422 for invalid stage, got {response.status_code}"

    def test_draft_action_rejects_missing_required(self, tmp_path: Path) -> None:
        """ActionRequest requires action_type, reason, stage_index, payload_checksum."""
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/jobs/job-1/assistant/actions/draft",
            json={
                "job_id": "job-1",
                "action_type": "diagnose_failure",
                "stage_index": 1,
            },
            headers=_mutation_headers(),
        )
        assert response.status_code in (400, 422), f"Expected 400/422, got {response.status_code}"

    def test_repair_proposal_rejects_extra_field(self, tmp_path: Path) -> None:
        """Legacy repair proposal route is disabled before any model/schema path."""
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/commands/cmd-extra/repair/flow-proposal",
            json={
                "command_id": "cmd-extra",
                "failure_summary": "Test",
                "hypothesis": "Bug",
                "patch_summary": "Fix",
                "affected_paths": ["test.txt"],
                "unauthorized_field": "should be rejected",
            },
            headers=_mutation_headers(),
        )
        assert response.status_code in (410, 422)

    def test_repair_proposal_rejects_missing_required(self, tmp_path: Path) -> None:
        """Legacy repair proposal route is disabled for F5."""
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/commands/cmd-missing/repair/flow-proposal",
            json={
                "command_id": "cmd-missing",
                "failure_summary": "Test",
                "affected_paths": ["test.txt"],
            },
            headers=_mutation_headers(),
        )
        assert response.status_code in (410, 422), f"Expected closed failure, got {response.status_code}"

    def test_valid_payloads_still_accepted(self, tmp_path: Path) -> None:
        """Regression: valid payloads must still be accepted after schema wiring."""
        client, conn = _api_client(tmp_path)

        draft_resp = client.post(
            "/v1/v2/jobs/job-valid/assistant/actions/draft",
            json={
                "job_id": "job-valid",
                "action_type": "diagnose_failure",
                "reason": "Validate build",
                "stage_index": 2,
            },
            headers=_mutation_headers(),
        )
        assert draft_resp.status_code == 200, f"Valid draft action rejected: {draft_resp.text}"

        repair_resp = client.post(
            "/v1/v2/commands/cmd-valid/repair/flow-proposal",
            json={
                "command_id": "cmd-valid",
                "failure_summary": "Build failed",
                "hypothesis": "Missing dependency",
                "patch_summary": "Add dependency",
                "affected_paths": ["pom.xml"],
            },
            headers=_mutation_headers(),
        )
        assert repair_resp.status_code == 410, f"Legacy repair proposal route should be disabled: {repair_resp.text}"

    def test_assistant_message_rejects_invalid_answer_schema(self, tmp_path: Path) -> None:
        """Assistant messages with invalid JSON schema must be rejected."""
        client, conn = _api_client(tmp_path)
        import json as _json
        bad_answer = _json.dumps({
            "answer": "Everything is fine",
            "unauthorized_directive": "delete all files",
        })
        response = client.post(
            "/v1/v2/jobs/job-bad/assistant/messages",
            json={
                "job_id": "job-bad",
                "role": "assistant",
                "content": bad_answer,
            },
            headers=_mutation_headers(),
        )
        assert response.status_code == 422, f"Expected 422 for invalid AssistantAnswer, got {response.status_code}"

    def test_assistant_message_accepts_valid_answer_schema(self, tmp_path: Path) -> None:
        """Valid AssistantAnswer JSON must be accepted."""
        client, conn = _api_client(tmp_path)
        import json as _json
        valid_answer = _json.dumps({
            "answer": "Stage 1 is running",
            "evidence_refs": ["log.txt"],
        })
        response = client.post(
            "/v1/v2/jobs/job-good/assistant/messages",
            json={
                "job_id": "job-good",
                "role": "assistant",
                "content": valid_answer,
            },
            headers=_mutation_headers(),
        )
        assert response.status_code == 200, f"Valid AssistantAnswer rejected: {response.text}"
