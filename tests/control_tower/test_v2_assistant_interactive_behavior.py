"""Tests for V2 assistant interactive behavior — intent classification,
intent-aware fallback answers, root_pom preview resolution, capability
boundary responses, and conversation history safety."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import V2MigrationJobRecord


def _mutation_headers():
    from migration_factory.control_tower.adapters.fastapi.security import DEFAULT_FRONTEND_CLIENT_ID
    return {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": DEFAULT_FRONTEND_CLIENT_ID,
    }


def _client_with_setup(
    tmp_path: Path,
    *,
    output_dir: str | None = None,
    model_available: bool = False,
) -> tuple[TestClient, sqlite3.Connection, str]:
    from migration_factory.control_tower.adapters.fastapi import create_app
    from migration_factory.control_tower.application.v2_setup_service import CreateSetupRequest, V2SetupService
    from migration_factory.control_tower.application.v2_assistant_model_client import V2AssistantModelResult

    class _FakeModelClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def answer(
            self,
            *,
            prompt: str,
            fallback: str,
            conversation_history: list[dict[str, str]] | None = None,
        ) -> V2AssistantModelResult:
            self.calls.append({"prompt": prompt, "fallback": fallback, "conversation_history": conversation_history})
            return V2AssistantModelResult(
                content=fallback,
                source="deterministic",
                model_status="fallback",
                provider="deterministic",
                role="assistant",
                success=False,
                redacted_summary="fake fallback",
                failure_reason="test_mode",
            )

    conn = sqlite3.connect(
        tmp_path / "assistant_interactive.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_pending_migrations(conn)

    fake_model = _FakeModelClient()
    app = create_app(lambda: SqliteUnitOfWork(conn), v2_assistant_model_client=fake_model)
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    # Create setup
    output_dir_path = output_dir or str(tmp_path / "output")
    Path(output_dir_path).mkdir(parents=True, exist_ok=True)
    now = utc_now_text()
    with SqliteUnitOfWork(conn) as uow:
        svc = V2SetupService(SqliteUnitOfWork(conn).v2_setups)
        setup_dto = svc.create_setup(
            CreateSetupRequest(
                run_name="test-run-interactive",
                legacy_app_path=str(tmp_path / "legacy"),
                output_parent_path=output_dir_path,
                ai_hub_path=str(tmp_path / "ai-hub"),
                java11_home="/usr/lib/jvm/java-11",
                java17_home="/usr/lib/jvm/java-17",
                java21_home="/usr/lib/jvm/java-21",
                maven_cmd="mvn",
                proof_level="build_test_verified",
                skip_endpoint_smoke=True,
                migration_flags={},
                created_by="test",
            )
        )
        setup_id = setup_dto.setup_id
        uow.v2_jobs.save(
            V2MigrationJobRecord(
                job_id="job-interactive",
                setup_id=setup_id,
                setup_checksum="checksum",
                pipeline_id="springboot-216-to-356-java21-three-stage",
                stage_chain_json="[]",
                status="running",
                created_at=now,
                updated_at=now,
                correlation_id=None,
            )
        )

    return client, conn, setup_id


def _seed_stage_event(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    stage: int,
    event_type: str,
    status: str = "completed",
    payload: dict | None = None,
) -> None:
    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=stage,
            event_type=event_type,
            status=status,
            message=f"Stage {stage} {event_type}.",
            payload=payload or {},
        )


def _seed_stage_command(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    stage: int,
    command_id: str,
    sandbox_path: Path,
    status: str = "completed",
) -> None:
    from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import V2StageCommandRecord

    now = utc_now_text()
    with SqliteUnitOfWork(conn) as uow:
        uow.v2_commands.save(
            V2StageCommandRecord(
                command_id=command_id,
                job_id=job_id,
                stage_index=stage,
                manifest_checksum=f"manifest-{command_id}",
                argv_json=json.dumps(["modernizer", "--sandbox", str(sandbox_path)]),
                env_json="{}",
                status=status,
                created_at=now,
                updated_at=now,
                result_json=json.dumps({"sandbox_path": str(sandbox_path)}),
            )
        )


class TestIntentClassification:
    """Test _classify_v2_assistant_intent heuristic."""

    def test_explain_pom_triggers_pom_or_dependency(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("EXPLAIN THE POM") == "pom_or_dependency_explanation"
        assert _classify_v2_assistant_intent("explain the pom for stage 1") == "pom_or_dependency_explanation"
        assert _classify_v2_assistant_intent("describe the pom") == "pom_or_dependency_explanation"
        assert _classify_v2_assistant_intent("analyze the pom.xml") == "pom_or_dependency_explanation"
        assert _classify_v2_assistant_intent("summarize dependencies") == "pom_or_dependency_explanation"
        assert _classify_v2_assistant_intent("break down the pom") == "pom_or_dependency_explanation"

    def test_plugin_without_pom_is_artifact_content(self) -> None:
        """Bare plugin without pom context should be artifact_content."""
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("inspect the plugin") == "artifact_content"
        assert _classify_v2_assistant_intent("show me the openrewrite plugin xml") == "artifact_content"

    def test_you_can_change_triggers_capability_boundary(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("YOU CAN CHANGE") == "capability_boundary"
        assert _classify_v2_assistant_intent("can you change this?") == "capability_boundary"
        assert _classify_v2_assistant_intent("why can't you do it?") == "capability_boundary"
        assert _classify_v2_assistant_intent("just make the change") == "capability_boundary"

    def test_what_happened_triggers_status(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("what happened?") == "status"
        assert _classify_v2_assistant_intent("what is happening now") == "status"
        assert _classify_v2_assistant_intent("status?") == "status"
        assert _classify_v2_assistant_intent("what failed?") == "status"
        assert _classify_v2_assistant_intent("what should I do next?") == "status"
        assert _classify_v2_assistant_intent("approve stage 2") == "status"

    def test_model_questions_triggers_model_status(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("Is AI model connected?") == "model_status"
        assert _classify_v2_assistant_intent("azure status?") == "model_status"
        assert _classify_v2_assistant_intent("what provider?") == "model_status"

    def test_generic_questions_default_to_general(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("hello") == "general_question"
        assert _classify_v2_assistant_intent("how does migration work?") == "general_question"
        assert _classify_v2_assistant_intent("why use this tool?") == "general_question"

    def test_bare_how_or_why_not_artifact_triggers(self) -> None:
        """Bare 'how' or 'why' without artifact terms must not match artifact_content."""
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("how does this work?") == "general_question"
        assert _classify_v2_assistant_intent("why did it fail?") != "pom_or_dependency_explanation"


class TestExplainPomResolution:
    """Test that EXPLAIN THE POM questions resolve root_pom preview."""

    def test_explain_pom_resolves_root_pom_alias(self, tmp_path: Path) -> None:
        """EXPLAIN THE POM triggers root_pom preview resolution via new patterns."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(
            "<project><artifactId>stage-one</artifactId></project>",
            encoding="utf-8",
        )
        _seed_stage_command(conn, job_id="job-interactive", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-interactive", stage=1, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "EXPLAIN THE POM"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Should resolve root_pom and include content
        assert "stage-one" in content
        # Should NOT present rewrite_dry_run.patch as full pom.xml
        assert "rewrite_dry_run.patch" not in content

    def test_explain_pom_stage_1_resolves_stage_1(self, tmp_path: Path) -> None:
        """EXPLAIN THE POM FOR STAGE 1 resolves stage 1 root_pom."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox1 = tmp_path / "stage1-sandbox"
        sandbox2 = tmp_path / "stage2-sandbox"
        sandbox1.mkdir()
        sandbox2.mkdir()
        (sandbox1 / "pom.xml").write_text(
            "<project><artifactId>stage-one-only</artifactId></project>",
            encoding="utf-8",
        )
        (sandbox2 / "pom.xml").write_text(
            "<project><artifactId>stage-two-content</artifactId></project>",
            encoding="utf-8",
        )
        _seed_stage_command(conn, job_id="job-interactive", stage=1, command_id="cmd-s1", sandbox_path=sandbox1)
        _seed_stage_command(conn, job_id="job-interactive", stage=2, command_id="cmd-s2", sandbox_path=sandbox2)
        _seed_stage_event(conn, job_id="job-interactive", stage=1, event_type="stage_completed")
        _seed_stage_event(conn, job_id="job-interactive", stage=2, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "EXPLAIN THE POM FOR STAGE 1"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        assert "stage-one-only" in content
        assert "stage-two-content" not in content

    def test_explain_pom_no_full_checklist_when_exists(self, tmp_path: Path) -> None:
        """When root_pom exists=true, answer must NOT include full operational checklist."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(
            "<project><artifactId>checklist-test</artifactId></project>",
            encoding="utf-8",
        )
        _seed_stage_command(conn, job_id="job-interactive", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-interactive", stage=1, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "EXPLAIN THE POM"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Should have POM content
        assert "checklist-test" in content
        # Must NOT repeat stage status / next operator action / guardrails checklist
        checklist_phrases = [
            "Stage Status:",
            "Next operator action:",
            "Guardrails:",
        ]
        for phrase in checklist_phrases:
            assert phrase not in content, (
                f"POM explanation fallback must not include '{phrase}'. Got content:\n{content[:500]}"
            )

    def test_explain_pom_running_returns_short_unavailable(self, tmp_path: Path) -> None:
        """When stage is running, POM explanation returns short unavailable message."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text("<project />", encoding="utf-8")
        _seed_stage_command(
            conn, job_id="job-interactive", stage=1, command_id="cmd-s1",
            sandbox_path=sandbox, status="running",
        )
        _seed_stage_event(
            conn, job_id="job-interactive", stage=1,
            event_type="sandbox_transform_started", status="running",
        )

        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "EXPLAIN THE POM"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Should say not available
        assert "not available" in content
        # Should mention reason (stage running, underscored in code but displayed with spaces)
        assert "stage running" in content or "stage_running" in content
        # Must NOT be full status template
        assert "Stage Status:" not in content


class TestDependenciesUseRootPom:
    """Test that dependency questions use root_pom, not dependency_graph."""

    def test_describe_dependencies_uses_root_pom(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(
            "<project><artifactId>dep-test</artifactId>"
            "<dependencies><dependency><groupId>org.example</groupId>"
            "<artifactId>my-lib</artifactId></dependency></dependencies></project>",
            encoding="utf-8",
        )
        _seed_stage_command(conn, job_id="job-interactive", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-interactive", stage=1, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "DESCRIBE THE DEPENDENCIES"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Should use root_pom content
        assert "dep-test" in content
        # Should NOT fall back to dependency_graph
        assert "dependency_graph" not in content
        # Should NOT present rewrite as full pom
        assert "rewrite_dry_run.patch" not in content


class TestCapabilityBoundary:
    """Test 'you can change' responses."""

    def test_you_can_change_returns_capability_boundary(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)

        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "YOU CAN CHANGE"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Must include capability boundary explanation
        assert "cannot" in content.lower()
        assert "approve" in content.lower() or "execute" in content.lower() or "modify" in content.lower()
        # Must NOT be full stage status template
        assert "Stage Status:" not in content

    def test_capability_boundary_includes_can_do_list(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)

        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "why can't you do it?"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Must mention what it can do
        assert any(phrase in content.lower() for phrase in [
            "explain", "summarize", "compare", "draft",
        ])


class TestModelStatus:
    """Test model status questions."""

    def test_is_ai_model_connected_returns_model_status(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)

        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "Is AI model connected?"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Should mention model/provider info
        assert any(term in content.lower() for term in [
            "model", "azure", "openai", "configured", "fallback", "deterministic",
        ])
        # Should NOT be full stage status template
        assert "Stage Status:" not in content


class TestWhatHappened:
    """Test status questions still work."""

    def test_what_happened_returns_operational_status(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)

        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "What happened?"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        assert "currently running" in content
        assert "no newer operational event" in content
        assert "Stage Status:" not in content

    def test_what_is_happening_now_returns_operational_status(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        _seed_stage_event(conn, job_id="job-interactive", stage=1, event_type="stage_started", status="running")

        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "what is happening now"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        assert "Stage 1 stage transition is happening right now" in content
        assert "Stage 1 completion" in content
        assert "Next operator action:" not in content


class TestFallbackSafety:
    """Test fallback mode does not claim execution/write/approval abilities."""

    def test_fallback_does_not_claim_execution_ability(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)

        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "what can you do?"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"].lower()
        # Must NEVER claim execution ability
        forbidden = ["i can execute", "i can approve", "i can write", "i will approve"]
        for phrase in forbidden:
            assert phrase not in content, f"Fallback must not claim: {phrase}"

    def test_rewrite_patch_never_presented_as_full_pom(self, tmp_path: Path) -> None:
        """rewrite_dry_run.patch must never be presented as full pom.xml."""
        client, conn, _setup_id = _client_with_setup(tmp_path)

        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "EXPLAIN THE POM"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        assert "rewrite_dry_run.patch" not in content


class TestConversationHistory:
    """Test conversation history is included and bounded."""

    def test_conversation_history_bounded(self, tmp_path: Path) -> None:
        """After multiple messages, conversation history is bounded."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text("<project />", encoding="utf-8")
        _seed_stage_command(conn, job_id="job-interactive", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-interactive", stage=1, event_type="stage_completed")

        # Send multiple messages to build conversation history
        for i in range(10):
            client.post(
                "/v1/v2/jobs/job-interactive/assistant/ask",
                json={"question": f"message number {i}"},
                headers=_mutation_headers(),
            )

        # Final question should succeed with bounded history
        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "EXPLAIN THE POM"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        final_call = client.app.state.v2_assistant_model_client.calls[-1]
        assert final_call["conversation_history"] == []
        prompt = json.loads(final_call["prompt"])
        reference = prompt["conversation_reference"]
        assert reference["authority"] == "non_authoritative"
        assert reference["purpose"] == "reference_resolution_only"
        assert len(reference["recent_turns"]) == 6
        assert {turn["role"] for turn in reference["recent_turns"]} == {"user", "assistant"}
        assert reference["recent_turns"][-2]["content"] == "message number 9"


class TestNoSecretsLeak:
    """Test no raw paths/secrets leak in prompt, fallback, or response."""

    def test_no_raw_paths_in_response(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        secret_path = str(tmp_path / "secret-dir")
        (sandbox / "pom.xml").write_text(
            f"<project><path>{secret_path}</path></project>",
            encoding="utf-8",
        )
        _seed_stage_command(conn, job_id="job-interactive", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-interactive", stage=1, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "EXPLAIN THE POM"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Absolute paths must be redacted
        assert secret_path not in content
        # Token-like patterns must be redacted
        assert str(tmp_path) not in content


class TestArtifactPreviewPomResolution:
    """Test that root_pom resolves in assistant artifact preview flow."""

    def test_root_pom_preview_with_explain_triggers_resolution(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(
            "<project><artifactId>resolution-test</artifactId></project>",
            encoding="utf-8",
        )
        _seed_stage_command(conn, job_id="job-interactive", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-interactive", stage=1, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "explain what's in the pom.xml"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        assert "resolution-test" in content
        assert "rewrite_dry_run.patch" not in content


class TestStageSandboxResolutionAfterRedaction:
    """F12/F13 fix: sandbox_path must survive redaction so the resolver
    can find the stage sandbox after stage_completed.

    The orchestrator's _event() method runs all payloads through
    redact_public_value which historically destroyed absolute paths
    in any key containing "path".  The fix preserves sandbox_path
    so _resolve_stage_sandbox_root can find the completed stage's
    sandbox root even when the event payload has been through
    the full redaction pipeline.
    """

    def test_stage1_completed_stage2_running_resolves_stage1_pom(
        self, tmp_path: Path
    ) -> None:
        """Stage 1 completed + Stage 2 running → root_pom for Stage 1
        resolves with exists=true, not sandbox_unresolved or stage_running."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox1 = tmp_path / "stage1-sandbox"
        sandbox2 = tmp_path / "stage2-sandbox"
        sandbox1.mkdir()
        sandbox2.mkdir()
        (sandbox1 / "pom.xml").write_text(
            "<project><artifactId>stage-one-completed</artifactId></project>",
            encoding="utf-8",
        )
        (sandbox2 / "pom.xml").write_text(
            "<project><artifactId>stage-two-running</artifactId></project>",
            encoding="utf-8",
        )
        # Stage 1: completed with sandbox_path preserved (as it would be after fix)
        _seed_stage_command(
            conn, job_id="job-interactive", stage=1,
            command_id="cmd-s1", sandbox_path=sandbox1,
        )
        _seed_stage_event(
            conn, job_id="job-interactive", stage=1,
            event_type="stage_completed",
            payload={"command_id": "cmd-s1", "sandbox_path": str(sandbox1), "exit_code": 0},
        )
        # Stage 2: currently running (sandbox_transform_started with status=running)
        _seed_stage_command(
            conn, job_id="job-interactive", stage=2,
            command_id="cmd-s2", sandbox_path=sandbox2, status="running",
        )
        _seed_stage_event(
            conn, job_id="job-interactive", stage=2,
            event_type="sandbox_transform_started", status="running",
        )

        # Ask for Stage 1 POM
        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "give me the pom xml for stage 1"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Must resolve Stage 1 root_pom successfully
        assert "stage-one-completed" in content
        # Must NOT return stage_running (Stage 2 running should not affect Stage 1)
        assert "stage_running" not in content
        # Must NOT return sandbox_unresolved
        assert "sandbox unresolved" not in content and "sandbox_unresolved" not in content

    def test_stage2_running_reports_stage_running_for_stage2(
        self, tmp_path: Path
    ) -> None:
        """When asking for Stage 2 and Stage 2 is running, resolver must
        return exists=false reason=stage_running."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage2-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text("<project />", encoding="utf-8")
        _seed_stage_command(
            conn, job_id="job-interactive", stage=2,
            command_id="cmd-s2", sandbox_path=sandbox, status="running",
        )
        _seed_stage_event(
            conn, job_id="job-interactive", stage=2,
            event_type="sandbox_transform_started", status="running",
        )

        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "give me the pom xml for stage 2"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Must indicate stage is running / not available
        assert "stage running" in content or "stage_running" in content or "not available" in content

    def test_stage1_completed_sandbox_artifact_registered_direct_endpoint(
        self, tmp_path: Path
    ) -> None:
        """Direct endpoint /files/root-pom?stage=1 returns pom.xml
        when sandbox artifact event is registered."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(
            "<project><artifactId>direct-endpoint</artifactId></project>",
            encoding="utf-8",
        )
        # Command with sandbox_path
        _seed_stage_command(
            conn, job_id="job-interactive", stage=1,
            command_id="cmd-s1", sandbox_path=sandbox,
        )
        # Simulate the "Stage sandbox output registered" event
        _seed_stage_event(
            conn, job_id="job-interactive", stage=1,
            event_type="artifact_written",
            payload={
                "command_id": "cmd-s1",
                "artifact_kind": "sandbox",
                "relative_path": str(sandbox),
            },
        )
        # Also seed stage_completed with sandbox_path
        _seed_stage_event(
            conn, job_id="job-interactive", stage=1,
            event_type="stage_completed",
            payload={
                "command_id": "cmd-s1",
                "sandbox_path": str(sandbox),
                "exit_code": 0,
            },
        )

        response = client.get(
            "/v1/v2/jobs/job-interactive/files/root-pom?stage=1&mode=preview",
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["exists"] is True, f"Expected exists=True, got {body}"
        assert "direct-endpoint" in body["content"]
        assert body["reason"] is None

    def test_missing_pom_xml_in_sandbox_reports_file_missing(
        self, tmp_path: Path
    ) -> None:
        """When sandbox artifact exists but pom.xml is missing, reason must
        be file_missing_or_unsafe, not sandbox_unresolved."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        # Do NOT create pom.xml — sandbox exists but no pom.xml
        _seed_stage_command(
            conn, job_id="job-interactive", stage=1,
            command_id="cmd-s1", sandbox_path=sandbox,
        )
        _seed_stage_event(
            conn, job_id="job-interactive", stage=1,
            event_type="stage_completed",
            payload={
                "command_id": "cmd-s1",
                "sandbox_path": str(sandbox),
                "exit_code": 0,
            },
        )

        response = client.get(
            "/v1/v2/jobs/job-interactive/files/root-pom?stage=1",
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["exists"] is False
        assert body["reason"] == "file_missing_or_unsafe", (
            f"Expected file_missing_or_unsafe, got {body['reason']}"
        )

    def test_symlink_sandbox_path_reports_file_missing_or_unsafe(
        self, tmp_path: Path
    ) -> None:
        """When sandbox path passes safety but pom.xml is a symlink
        escape, reason is file_missing_or_unsafe."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        outside = tmp_path / "outside"
        sandbox.mkdir()
        outside.mkdir()
        (outside / "pom.xml").write_text(
            "<project><secret>symlink-escape</secret></project>",
            encoding="utf-8",
        )
        try:
            (sandbox / "pom.xml").symlink_to(outside / "pom.xml")
        except OSError as exc:
            pytest.skip(f"Symlink creation privilege unavailable: {exc}")
        _seed_stage_command(
            conn, job_id="job-interactive", stage=1,
            command_id="cmd-s1", sandbox_path=sandbox,
        )
        _seed_stage_event(
            conn, job_id="job-interactive", stage=1,
            event_type="stage_completed",
            payload={
                "command_id": "cmd-s1",
                "sandbox_path": str(sandbox),
                "exit_code": 0,
            },
        )

        response = client.get(
            "/v1/v2/jobs/job-interactive/files/root-pom?stage=1",
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["exists"] is False
        assert body["reason"] == "file_missing_or_unsafe"
        assert "symlink-escape" not in body.get("content", "")

    def test_no_sandbox_evidence_reports_sandbox_unresolved(
        self, tmp_path: Path
    ) -> None:
        """When no sandbox evidence exists at all, reason must be
        sandbox_unresolved."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        # No sandbox command seeded, no stage_completed with sandbox_path
        _seed_stage_event(
            conn, job_id="job-interactive", stage=1,
            event_type="sandbox_transform_completed",
            payload={"command_id": "cmd-nonexistent"},
        )

        response = client.get(
            "/v1/v2/jobs/job-interactive/files/root-pom?stage=1",
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["exists"] is False
        assert body["reason"] == "sandbox_unresolved", (
            f"Expected sandbox_unresolved, got {body['reason']}"
        )


class TestRedactionPreservesSandboxPath:
    """Unit tests verifying redaction preserves backend-owned sandbox_path."""

    def test_sandbox_path_survives_redact_public_value(self) -> None:
        """redact_public_value must preserve sandbox_path absolute values
        so the stage sandbox resolver can reconstruct the sandbox root."""
        from migration_factory.control_tower.application.redaction import (
            redact_public_value,
        )
        payload = {
            "command_id": "cmd-abc123",
            "sandbox_path": "/home/ubuntu/stage1-sandbox",
            "exit_code": 0,
        }
        redacted = redact_public_value(payload)
        assert isinstance(redacted, dict)
        assert redacted["sandbox_path"] == "/home/ubuntu/stage1-sandbox", (
            f"sandbox_path must survive redaction, got {redacted['sandbox_path']!r}"
        )
        assert redacted["command_id"] == "cmd-abc123"
        assert redacted["exit_code"] == 0

    def test_sandbox_path_with_traversal_is_redacted(self) -> None:
        """sandbox_path containing '..' traversal must still be redacted."""
        from migration_factory.control_tower.application.redaction import (
            redact_public_value,
        )
        payload = {
            "sandbox_path": "/home/../etc/passwd",
        }
        redacted = redact_public_value(payload)
        assert isinstance(redacted, dict)
        assert redacted["sandbox_path"] == "[redacted]", (
            f"Traversal path must be redacted, got {redacted['sandbox_path']!r}"
        )

    def test_other_path_keys_still_redacted(self) -> None:
        """Keys like relative_path, artifact_path must still have their
        absolute paths redacted."""
        from migration_factory.control_tower.application.redaction import (
            redact_public_value,
        )
        payload = {
            "relative_path": "/home/ubuntu/some/file.xml",
            "artifact_path": "/etc/secret",
            "message": "check /tmp/output",
        }
        redacted = redact_public_value(payload)
        assert isinstance(redacted, dict)
        # relative_path and artifact_path contain "path" → redact_absolute_paths
        assert "/home/ubuntu" not in redacted.get("relative_path", "")
        assert "/etc/secret" not in redacted.get("artifact_path", "")
        # message goes through full redact_model_summary
        assert "/tmp/output" not in redacted.get("message", "")

    def test_sandbox_path_preserved_in_nested_dict(self) -> None:
        """sandbox_path inside nested dicts (e.g., inside artifact_refs)
        must also be preserved."""
        from migration_factory.control_tower.application.redaction import (
            redact_public_value,
        )
        payload = {
            "artifact_refs": {
                "sandbox_path": "/home/ubuntu/stage2-sandbox",
            },
        }
        redacted = redact_public_value(payload)
        assert isinstance(redacted, dict)
        refs = redacted.get("artifact_refs", {})
        assert isinstance(refs, dict)
        # artifact_refs is a dict, so each key-value goes through _redact_dict_value
        assert refs.get("sandbox_path") == "/home/ubuntu/stage2-sandbox", (
            f"Nested sandbox_path must survive, got {refs.get('sandbox_path')!r}"
        )


class TestPublicOutputNeverExposesSandboxPath:
    """F12/F13 public-boundary safety: sandbox_path must be preserved in DB
    for internal backend resolution, but must NEVER appear in public API
    responses, SSE streams, cockpit event panels, or assistant prompts.

    The DB persistence path (redact_public_value from redaction.py) preserves
    sandbox_path.  The public output path (redact_public_data from security.py)
    is a separate redaction pipeline that must still redact absolute
    filesystem paths.
    """

    def test_public_events_snapshot_does_not_expose_sandbox_path(
        self, tmp_path: Path
    ) -> None:
        """Public /events/snapshot endpoint must redact sandbox_path from
        event payloads.  The raw absolute path must never reach the browser."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text("<project />", encoding="utf-8")

        sandbox_abs = str(sandbox.resolve())

        # Seed stage_completed event with raw sandbox_path
        # (simulates what the orchestrator now persists after our fix)
        _seed_stage_event(
            conn,
            job_id="job-interactive",
            stage=1,
            event_type="stage_completed",
            payload={
                "command_id": "cmd-s1",
                "sandbox_path": sandbox_abs,
                "exit_code": 0,
            },
        )

        # Call the public events snapshot endpoint
        response = client.get(
            "/v1/v2/migration-jobs/job-interactive/events/snapshot?after=0",
        )

        assert response.status_code == 200, response.text
        body = response.json()
        events = body.get("events", [])
        assert len(events) >= 1, "Expected at least one event in snapshot"

        # Serialize the full response body to string for grep-style check
        body_text = json.dumps(body, sort_keys=True)

        # The raw absolute sandbox path must NOT appear anywhere
        assert sandbox_abs not in body_text, (
            f"Absolute sandbox path leak in public events snapshot:\n{sandbox_abs}\n\n"
            f"Response excerpt: {body_text[:800]}"
        )
        # [redacted-path] should appear instead
        assert "[redacted-path]" in body_text or "[redacted" in body_text, (
            "Expected redaction placeholder in public events snapshot, "
            f"got:\n{body_text[:800]}"
        )

    def test_sse_event_serialization_does_not_expose_sandbox_path(
        self, tmp_path: Path
    ) -> None:
        """SSE event stream must redact sandbox_path.  The raw absolute
        path must never appear in EventSource output."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage2-sandbox"
        sandbox.mkdir()

        sandbox_abs = str(sandbox.resolve())

        # Seed events that would be streamed via SSE
        _seed_stage_event(
            conn,
            job_id="job-interactive",
            stage=2,
            event_type="build_completed",
            payload={
                "command_id": "cmd-s2",
                "sandbox_path": sandbox_abs,
                "build_status": "BUILD_PASSED_IN_SANDBOX",
            },
        )

        # Use the once=true snapshot which uses the same _v2_event_payload
        # serialization path as the SSE stream
        response = client.get(
            "/v1/v2/migration-jobs/job-interactive/events?once=true&after=0",
        )

        assert response.status_code == 200, response.text
        sse_text = response.text

        # The raw absolute sandbox path must NOT appear in SSE output
        assert sandbox_abs not in sse_text, (
            f"Absolute sandbox path leak in SSE event stream:\n{sandbox_abs}\n\n"
            f"SSE excerpt: {sse_text[:800]}"
        )
        # Redaction placeholder should appear
        assert "[redacted-path]" in sse_text or "[redacted" in sse_text, (
            "Expected redaction placeholder in SSE stream, "
            f"got:\n{sse_text[:800]}"
        )

    def test_assistant_prompt_does_not_include_raw_sandbox_path(
        self, tmp_path: Path
    ) -> None:
        """The prompt JSON sent to the model must not include the raw
        absolute sandbox_path.  Only redacted preview content and metadata."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(
            "<project><artifactId>prompt-safety-test</artifactId></project>",
            encoding="utf-8",
        )

        sandbox_abs = str(sandbox.resolve())

        _seed_stage_command(
            conn, job_id="job-interactive", stage=1,
            command_id="cmd-s1", sandbox_path=sandbox,
        )
        _seed_stage_event(
            conn, job_id="job-interactive", stage=1,
            event_type="stage_completed",
            payload={
                "command_id": "cmd-s1",
                "sandbox_path": sandbox_abs,
                "exit_code": 0,
            },
        )

        # Send a question that triggers root_pom resolution
        # The fake model client stores the prompt in self.calls
        response = client.post(
            "/v1/v2/jobs/job-interactive/assistant/ask",
            json={"question": "EXPLAIN THE POM"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text

        # The assistant response content must not leak sandbox_path
        content = response.json()["assistant_message"]["content"]
        assert sandbox_abs not in content, (
            f"Sandbox path leak in assistant response:\n{sandbox_abs}\n\n"
            f"Content: {content[:500]}"
        )
        # The response should have the POM content (redacted)
        assert "prompt-safety-test" in content

    def test_direct_download_mode_redacts_sandbox_path_in_content(
        self, tmp_path: Path
    ) -> None:
        """Download mode must not leak the sandbox path in the XML content
        or response headers (beyond the expected Content-Disposition)."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        # Include the sandbox path inside the pom.xml content itself
        sandbox_abs = str(sandbox.resolve())
        (sandbox / "pom.xml").write_text(
            f"<project><secretPath>{sandbox_abs}</secretPath></project>",
            encoding="utf-8",
        )

        _seed_stage_command(
            conn, job_id="job-interactive", stage=1,
            command_id="cmd-s1", sandbox_path=sandbox,
        )
        _seed_stage_event(
            conn, job_id="job-interactive", stage=1,
            event_type="stage_completed",
            payload={
                "command_id": "cmd-s1",
                "sandbox_path": sandbox_abs,
                "exit_code": 0,
            },
        )

        response = client.get(
            "/v1/v2/jobs/job-interactive/files/root-pom?stage=1&mode=download",
        )

        assert response.status_code == 200, response.text
        body_text = response.text

        # Raw absolute sandbox path must NOT appear in download body
        assert sandbox_abs not in body_text, (
            f"Sandbox path leak in download body:\n{sandbox_abs}\n\n"
            f"Body: {body_text[:500]}"
        )
        # Content-Type should be XML
        content_type = response.headers.get("content-type", "")
        assert "xml" in content_type.lower()

    def test_cockpit_event_payload_never_exposes_sandbox_path(
        self, tmp_path: Path
    ) -> None:
        """The cockpit event panel (fed by /events/snapshot) must never
        expose the raw absolute sandbox_path in any event payload.

        This covers the full public event rendering path including
        pipeline projection and failure summary endpoints."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text("<project />", encoding="utf-8")

        sandbox_abs = str(sandbox.resolve())

        # Seed multiple event types that carry sandbox_path
        for evt_type in ("stage_completed", "sandbox_transform_completed", "build_completed"):
            _seed_stage_event(
                conn,
                job_id="job-interactive",
                stage=1,
                event_type=evt_type,
                payload={
                    "command_id": "cmd-s1",
                    "sandbox_path": sandbox_abs,
                },
            )

        # Test events snapshot
        resp_events = client.get(
            "/v1/v2/migration-jobs/job-interactive/events/snapshot?after=0",
        )
        assert resp_events.status_code == 200
        events_text = json.dumps(resp_events.json(), sort_keys=True)
        assert sandbox_abs not in events_text, (
            f"Sandbox path leak in cockpit events snapshot.\n{events_text[:800]}"
        )

        # Test pipeline projection
        resp_pipeline = client.get(
            "/v1/v2/migration-jobs/job-interactive/pipeline",
        )
        assert resp_pipeline.status_code == 200
        pipeline_text = json.dumps(resp_pipeline.json(), sort_keys=True)
        assert sandbox_abs not in pipeline_text, (
            f"Sandbox path leak in pipeline projection.\n{pipeline_text[:800]}"
        )

        # Test failure summary
        resp_failure = client.get(
            "/v1/v2/migration-jobs/job-interactive/failure-summary",
        )
        assert resp_failure.status_code == 200
        failure_text = json.dumps(resp_failure.json(), sort_keys=True)
        assert sandbox_abs not in failure_text, (
            f"Sandbox path leak in failure summary.\n{failure_text[:800]}"
        )
