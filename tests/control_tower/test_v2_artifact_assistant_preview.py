"""Tests for V2 artifact preview whitelist, assistant artifact-content
questions, and safe artifact resolution from backend events."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.domain.checksums import utc_now_text


def _mutation_headers():
    from migration_factory.control_tower.adapters.fastapi.security import DEFAULT_FRONTEND_CLIENT_ID
    return {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": DEFAULT_FRONTEND_CLIENT_ID,
    }


def _client_with_setup(tmp_path: Path, *, output_dir: str | None = None) -> tuple[TestClient, sqlite3.Connection, str]:
    from migration_factory.control_tower.adapters.fastapi import create_app
    from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import V2MigrationJobRecord
    from migration_factory.control_tower.application.v2_setup_service import CreateSetupRequest, V2SetupService

    conn = sqlite3.connect(
        tmp_path / "artifact_assistant_preview.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_pending_migrations(conn)

    app = create_app(lambda: SqliteUnitOfWork(conn))
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    # Create setup
    output_dir_path = output_dir or str(tmp_path / "output")
    Path(output_dir_path).mkdir(parents=True, exist_ok=True)
    now = utc_now_text()
    with SqliteUnitOfWork(conn) as uow:
        svc = V2SetupService(SqliteUnitOfWork(conn).v2_setups)
        setup_dto = svc.create_setup(
            CreateSetupRequest(
                run_name="test-run",
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
                job_id="job-artifact",
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


def _seed_artifact_event(conn: sqlite3.Connection, *, job_id: str, stage: int, artifact_kind: str, relative_path: str) -> None:
    """Seed an artifact_written event."""
    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=stage,
            event_type="artifact_written",
            status="completed",
            message=f"Artifact {artifact_kind} written.",
            payload={"artifact_kind": artifact_kind, "relative_path": relative_path},
        )


def _seed_stage_event(conn: sqlite3.Connection, *, job_id: str, stage: int, event_type: str, status: str = "completed", payload: dict | None = None) -> None:
    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=stage,
            event_type=event_type,
            status=status,
            message=f"Stage {stage} {event_type}.",
            payload=payload or {},
        )


def _seed_stage_command(conn: sqlite3.Connection, *, job_id: str, stage: int, command_id: str, sandbox_path: Path, status: str = "completed") -> None:
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


class TestArtifactPreviewWhitelist:
    """Test that safe_kinds whitelist works correctly."""

    def test_artifact_preview_allows_openrewrite_plugin_xml_when_event_ref_exists(
        self, tmp_path: Path
    ) -> None:
        """openrewrite_plugin_xml must be in safe_kinds and accessible."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        _seed_artifact_event(
            conn, job_id="job-artifact", stage=1,
            artifact_kind="openrewrite_plugin_xml",
            relative_path="openrewrite/plugin.xml",
        )

        response = client.get(
            "/v1/v2/jobs/job-artifact/artifacts/openrewrite_plugin_xml",
        )

        assert response.status_code != 400  # Not rejected as unknown kind
        body = response.json()
        assert body["artifact_kind"] == "openrewrite_plugin_xml"

    def test_artifact_preview_allows_approved_plan_lock_when_event_ref_exists(
        self, tmp_path: Path
    ) -> None:
        """approved_plan_lock must be in safe_kinds and accessible."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        _seed_artifact_event(
            conn, job_id="job-artifact", stage=2,
            artifact_kind="approved_plan_lock",
            relative_path=".migration/approved_plan.lock",
        )

        response = client.get(
            "/v1/v2/jobs/job-artifact/artifacts/approved_plan_lock",
        )

        assert response.status_code != 400
        body = response.json()
        assert body["artifact_kind"] == "approved_plan_lock"

    def test_artifact_preview_rejects_unknown_kind(
        self, tmp_path: Path
    ) -> None:
        """Unknown artifact kinds must be rejected with 400."""
        client, conn, _setup_id = _client_with_setup(tmp_path)

        response = client.get(
            "/v1/v2/jobs/job-artifact/artifacts/malicious_file",
        )

        assert response.status_code == 400
        body = response.json()
        error = body.get("error", {})
        assert "UNKNOWN_ARTIFACT_KIND" in error.get("code", "")

    def test_artifact_preview_rejects_path_like_kind(
        self, tmp_path: Path
    ) -> None:
        """Path-like artifact kinds must be rejected (400 for unknown, or 404 if URL-routed away)."""
        client, conn, _setup_id = _client_with_setup(tmp_path)

        # Path-like kinds without '../' reach the handler and get 400
        for path_like in ("/etc/passwd", "C:\\\\Windows\\\\System32"):
            response = client.get(
                f"/v1/v2/jobs/job-artifact/artifacts/{path_like}",
            )
            # Either 400 (handler rejects) or 404 (URL normalization rejects) is safe
            assert response.status_code in (400, 404), f"Unexpected status for {path_like}: {response.status_code}"
            if response.status_code == 400:
                error = response.json().get("error", {})
                assert "UNKNOWN_ARTIFACT_KIND" in error.get("code", "")

    def test_artifact_preview_with_stage_filter(
        self, tmp_path: Path
    ) -> None:
        """Stage query param should filter artifacts to matching stage."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        _seed_artifact_event(
            conn, job_id="job-artifact", stage=1,
            artifact_kind="phase2_log",
            relative_path="stage1/phase2.log",
        )
        _seed_artifact_event(
            conn, job_id="job-artifact", stage=2,
            artifact_kind="phase2_log",
            relative_path="stage2/phase2.log",
        )

        # Without stage filter, should find the latest (stage 2)
        response = client.get(
            "/v1/v2/jobs/job-artifact/artifacts/phase2_log",
        )
        assert response.status_code != 400

        # With stage=1 filter
        response_stage1 = client.get(
            "/v1/v2/jobs/job-artifact/artifacts/phase2_log?stage=1",
        )
        assert response_stage1.status_code != 400


class TestAssistantArtifactQuestions:
    """Test that assistant handles artifact-content questions correctly."""

    def test_assistant_does_not_invent_missing_pom(
        self, tmp_path: Path
    ) -> None:
        """When no POM artifact exists, assistant must not claim a full POM."""
        client, conn, _setup_id = _client_with_setup(tmp_path)

        response = client.post(
            "/v1/v2/jobs/job-artifact/assistant/ask",
            json={"question": "give me the full pom xml for stage 2"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        content = body["assistant_message"]["content"].lower()
        # Must NOT claim to have the full POM
        assert "<?xml" not in content or "not persisted" in content or "not available" in content
        # Should mention available artifact kinds or lack thereof
        assert "artifact" in content

    def test_assistant_artifact_question_returns_preview_when_safe_artifact_exists(
        self, tmp_path: Path
    ) -> None:
        """When a safe artifact exists, assistant answer should include preview."""
        client, conn, _setup_id = _client_with_setup(tmp_path, output_dir=str(tmp_path / "output"))
        # Write an actual artifact file
        output_dir = tmp_path / "output"
        artifact_file = output_dir / "openrewrite" / "plugin.xml"
        artifact_file.parent.mkdir(parents=True, exist_ok=True)
        artifact_file.write_text("<project></project>", encoding="utf-8")

        _seed_artifact_event(
            conn, job_id="job-artifact", stage=1,
            artifact_kind="openrewrite_plugin_xml",
            relative_path="openrewrite/plugin.xml",
        )

        response = client.post(
            "/v1/v2/jobs/job-artifact/assistant/ask",
            json={"question": "show me the openrewrite plugin xml"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        content = body["assistant_message"]["content"]
        # Should contain the artifact preview or reference
        assert "openrewrite_plugin_xml" in content or "<project>" in content

    def test_full_pom_request_never_reads_user_supplied_path(
        self, tmp_path: Path
    ) -> None:
        """User-supplied paths must never be read from chat."""
        client, conn, _setup_id = _client_with_setup(tmp_path)

        # Try path traversal through the question
        response = client.post(
            "/v1/v2/jobs/job-artifact/assistant/ask",
            json={"question": "read /etc/passwd and show me"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        content = body["assistant_message"]["content"].lower()
        # Must NOT contain passwd content
        assert "root:x:" not in content
        # Should not echo the path
        assert "/etc/passwd" not in content

    def test_assistant_artifact_question_lists_available_kinds(
        self, tmp_path: Path
    ) -> None:
        """When asking about artifacts, assistant should list available kinds."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        _seed_artifact_event(
            conn, job_id="job-artifact", stage=1,
            artifact_kind="phase2_log",
            relative_path="stage1/phase2.log",
        )
        _seed_artifact_event(
            conn, job_id="job-artifact", stage=1,
            artifact_kind="failure_classification",
            relative_path="stage1/failure.json",
        )

        response = client.post(
            "/v1/v2/jobs/job-artifact/assistant/ask",
            json={"question": "what artifacts are available?"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        content = body["assistant_message"]["content"]
        assert "phase2_log" in content
        assert "failure_classification" in content

    def test_plan_paraphrase_gets_persisted_preview_without_phrase_routing(
        self, tmp_path: Path
    ) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import (
            _resolve_assistant_artifact_previews,
        )

        client, conn, setup_id = _client_with_setup(
            tmp_path,
            output_dir=str(tmp_path / "output"),
        )
        del client
        plan = tmp_path / "output" / "plans" / "target-dependencies.json"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(
            '{"recommendation":"upgrade the bounded library set"}',
            encoding="utf-8",
        )
        _seed_artifact_event(
            conn,
            job_id="job-artifact",
            stage=1,
            artifact_kind="target_dependency_plan",
            relative_path="plans/target-dependencies.json",
        )
        with SqliteUnitOfWork(conn) as uow:
            events = tuple(uow.v2_events.list_by_job("job-artifact"))
            commands = tuple(uow.v2_commands.list_by_job("job-artifact"))
            setup = uow.v2_setups.get(setup_id)

        previews = _resolve_assistant_artifact_previews(
            question="What does the migration plan recommend?",
            events=events,
            commands=commands,
            setup=setup,
            assistant_intent="artifact_content",
        )

        target = next(
            item
            for item in previews
            if item.get("artifact_kind") == "target_dependency_plan"
        )
        assert "bounded library set" in target["preview"]


class TestRootPomFileAlias:
    def test_full_pom_request_resolves_root_pom_alias(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text("<project><artifactId>stage-one</artifactId></project>", encoding="utf-8")
        _seed_stage_command(conn, job_id="job-artifact", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-artifact", stage=1, event_type="sandbox_transform_completed")

        response = client.post(
            "/v1/v2/jobs/job-artifact/assistant/ask",
            json={"question": "give me the full pom.xml for stage 1"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        assert "stage-one" in content
        assert "rewrite_dry_run.patch" not in content

    def test_full_pom_request_stage_1_uses_stage_1_sandbox_not_latest(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox1 = tmp_path / "stage1-sandbox"
        sandbox2 = tmp_path / "stage2-sandbox"
        sandbox1.mkdir()
        sandbox2.mkdir()
        (sandbox1 / "pom.xml").write_text("<project><artifactId>stage-one</artifactId></project>", encoding="utf-8")
        (sandbox2 / "pom.xml").write_text("<project><artifactId>stage-two</artifactId></project>", encoding="utf-8")
        _seed_stage_command(conn, job_id="job-artifact", stage=1, command_id="cmd-s1", sandbox_path=sandbox1)
        _seed_stage_command(conn, job_id="job-artifact", stage=2, command_id="cmd-s2", sandbox_path=sandbox2)
        _seed_stage_event(conn, job_id="job-artifact", stage=1, event_type="sandbox_transform_completed")
        _seed_stage_event(conn, job_id="job-artifact", stage=2, event_type="sandbox_transform_completed")

        response = client.get("/v1/v2/jobs/job-artifact/files/root-pom?stage=1")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["exists"] is True
        assert "stage-one" in body["content"]
        assert "stage-two" not in body["content"]
        assert body["source_ref"] == {"command_id": "cmd-s1", "source": "command_result"}

    def test_full_pom_request_returns_full_content_when_file_exists(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        expected = "<project><modelVersion>4.0.0</modelVersion></project>"
        (sandbox / "pom.xml").write_text(expected, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-artifact", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-artifact", stage=1, event_type="stage_completed")

        response = client.get("/v1/v2/jobs/job-artifact/files/root-pom?stage=1")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["exists"] is True
        assert body["content"] == expected
        assert body["truncated"] is False

    def test_full_pom_request_returns_download_url_when_truncated(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text("<project>" + ("x" * 40000) + "</project>", encoding="utf-8")
        _seed_stage_command(conn, job_id="job-artifact", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-artifact", stage=1, event_type="stage_completed")

        response = client.get("/v1/v2/jobs/job-artifact/files/root-pom?stage=1")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["exists"] is True
        assert body["truncated"] is True
        assert body["download_url"] == "/v1/v2/jobs/job-artifact/files/root-pom?stage=1&mode=download"

    def test_full_pom_request_does_not_substitute_rewrite_patch_for_full_pom(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path, output_dir=str(tmp_path / "output"))
        output_dir = tmp_path / "output"
        patch_file = output_dir / "rewrite_dry_run.patch"
        patch_file.write_text("--- pom.xml\n+++ pom.xml\n", encoding="utf-8")
        _seed_artifact_event(
            conn,
            job_id="job-artifact",
            stage=1,
            artifact_kind="rewrite_dry_run.patch",
            relative_path="rewrite_dry_run.patch",
        )

        response = client.post(
            "/v1/v2/jobs/job-artifact/assistant/ask",
            json={"question": "give me the full pom xml for stage 1"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"].lower()
        assert "--- pom.xml" not in content
        assert "not available" in content
        assert "rewrite_dry_run.patch" in content

    def test_full_pom_request_stage_running_reports_not_available_yet(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text("<project />", encoding="utf-8")
        _seed_stage_command(conn, job_id="job-artifact", stage=1, command_id="cmd-s1", sandbox_path=sandbox, status="running")
        _seed_stage_event(conn, job_id="job-artifact", stage=1, event_type="sandbox_transform_started", status="running")

        response = client.get("/v1/v2/jobs/job-artifact/files/root-pom?stage=1")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["exists"] is False
        assert body["reason"] == "stage_running"
        assert body["content"] == ""

    def test_root_pom_endpoint_rejects_user_supplied_path(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)

        response = client.get("/v1/v2/jobs/job-artifact/files/root-pom?stage=1&path=pom.xml")

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "PATH_NOT_ACCEPTED"

    def test_root_pom_endpoint_rejects_path_traversal_and_symlink_escape(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        outside = tmp_path / "outside"
        sandbox.mkdir()
        outside.mkdir()
        (outside / "pom.xml").write_text("<project><secret>escape</secret></project>", encoding="utf-8")
        try:
            (sandbox / "pom.xml").symlink_to(outside / "pom.xml")
        except OSError as exc:
            pytest.skip(f"Windows symlink creation privilege unavailable: {exc}")
        _seed_stage_command(conn, job_id="job-artifact", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-artifact", stage=1, event_type="stage_completed")

        response = client.get("/v1/v2/jobs/job-artifact/files/root-pom?stage=1")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["exists"] is False
        assert body["reason"] == "file_missing_or_unsafe"
        assert "escape" not in body["content"]

    def test_root_pom_endpoint_redacts_secrets_and_absolute_paths(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(
            f"<project><token>sk-secret123</token><path>{tmp_path}</path></project>",
            encoding="utf-8",
        )
        _seed_stage_command(conn, job_id="job-artifact", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-artifact", stage=1, event_type="stage_completed")

        response = client.get("/v1/v2/jobs/job-artifact/files/root-pom?stage=1")

        assert response.status_code == 200, response.text
        body = response.json()
        assert "sk-secret123" not in body["content"]
        assert str(tmp_path) not in body["content"]


class TestArtifactPreviewPathSafety:
    """Test that artifact preview is safe from path traversal."""

    def test_artifact_preview_rejects_unknown_or_path_like_kind(
        self, tmp_path: Path
    ) -> None:
        """Reject all unknown and path-like artifact kinds (400 or safe 404)."""
        client, conn, _setup_id = _client_with_setup(tmp_path)

        dangerous_kinds = [
            "C:\\\\Windows\\\\System32\\\\config\\\\SAM",
            "/etc/shadow",
            "..%2f..%2fetc%2fpasswd",
            "....//....//etc//passwd",
        ]
        for kind in dangerous_kinds:
            response = client.get(
                f"/v1/v2/jobs/job-artifact/artifacts/{kind}",
            )
            # 400 = handler rejects unknown kind; 404 = URL normalization rejects before handler
            assert response.status_code in (400, 404), (
                f"Expected 400 or 404 for dangerous kind: {kind}, got {response.status_code}"
            )

    def test_artifact_preview_redacts_secrets(
        self, tmp_path: Path
    ) -> None:
        """Artifact preview must redact secrets and absolute paths."""
        client, conn, _setup_id = _client_with_setup(tmp_path, output_dir=str(tmp_path / "output"))
        output_dir = tmp_path / "output"
        artifact_file = output_dir / "repair_plan"
        artifact_file.write_text(
            "API_KEY=sk-abc123\nsecret=my-secret\n/home/user/project\n",
            encoding="utf-8",
        )

        _seed_artifact_event(
            conn, job_id="job-artifact", stage=1,
            artifact_kind="repair_plan",
            relative_path="repair_plan",
        )

        response = client.get(
            "/v1/v2/jobs/job-artifact/artifacts/repair_plan",
        )

        assert response.status_code != 400, response.text
        body = response.json()
        if body.get("exists"):
            preview = body.get("preview", "")
            assert "sk-abc123" not in preview
            assert "my-secret" not in preview


class TestRootPomAssistantLiveTranscript:
    """F12: Assistant live-transcript behaviour for root_pom file alias."""

    def test_assistant_stage_running_says_unavailable_due_to_stage_running(
        self, tmp_path: Path
    ) -> None:
        """When stage is running and user asks for full pom.xml, assistant says
        root_pom is unavailable because stage is running — not 'I cannot create files'."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text("<project />", encoding="utf-8")
        _seed_stage_command(conn, job_id="job-artifact", stage=1, command_id="cmd-s1", sandbox_path=sandbox, status="running")
        _seed_stage_event(conn, job_id="job-artifact", stage=1, event_type="sandbox_transform_started", status="running")

        response = client.post(
            "/v1/v2/jobs/job-artifact/assistant/ask",
            json={"question": "give me the full pom.xml for stage 1"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"].lower()
        # Must NOT claim to have the full POM
        assert "<?xml" not in content or "not available" in content
        # Must NOT say "I cannot create files" (the old incorrect behaviour)
        assert "cannot create" not in content
        # Should reference the root_pom alias or explain unavailability reason
        assert "stage_running" in content or "running" in content or "not available" in content
        # Should NOT suggest rewrite_dry_run.patch as substitute
        assert "rewrite_dry_run.patch" not in content

    def test_assistant_stage_completed_includes_root_pom_content(
        self, tmp_path: Path
    ) -> None:
        """When stage is completed and pom.xml exists, assistant includes actual
        backend-resolved root pom content in its answer."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        expected = "<project><artifactId>stage-one</artifactId></project>"
        (sandbox / "pom.xml").write_text(expected, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-artifact", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-artifact", stage=1, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-artifact/assistant/ask",
            json={"question": "give me the full pom.xml for stage 1"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Should include the actual pom content (stage-one artifact ID)
        assert "stage-one" in content
        # Should NOT substitute rewrite patch
        assert "rewrite_dry_run.patch" not in content
        # Should NOT say the pom is unavailable (it is available)
        assert "not available" not in content.lower()

    def test_assistant_dependencies_question_uses_root_pom_not_fallback(
        self, tmp_path: Path
    ) -> None:
        """When user asks 'full dependencies for pom xml stage 1', assistant uses
        root_pom content and does not fall back to dependency_graph unless
        root_pom is unavailable."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(
            "<project><artifactId>stage-one</artifactId>"
            "<dependencies><dependency><groupId>org.example</groupId>"
            "<artifactId>my-lib</artifactId></dependency></dependencies></project>",
            encoding="utf-8",
        )
        _seed_stage_command(conn, job_id="job-artifact", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-artifact", stage=1, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-artifact/assistant/ask",
            json={"question": "show me full dependencies for pom xml stage 1"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Should include root_pom content with the dependency info
        assert "my-lib" in content or "stage-one" in content
        # Should NOT fall back to dependency_graph (if root_pom is available)
        assert "dependency_graph" not in content
        # Should NOT suggest rewrite_dry_run.patch as substitute
        assert "rewrite_dry_run.patch" not in content


class TestRootPomDownloadSafety:
    """F12: download mode must redact content; never leak raw file."""

    def test_download_mode_redacts_secrets_and_paths(
        self, tmp_path: Path
    ) -> None:
        """mode=download returns redacted XML, same redaction policy as preview.
        Tokens and absolute paths must not leak."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        secret_path = str(tmp_path / "secret-dir")
        (sandbox / "pom.xml").write_text(
            f"<project><token>sk-secret789</token>"
            f"<path>{secret_path}</path></project>",
            encoding="utf-8",
        )
        _seed_stage_command(conn, job_id="job-artifact", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-artifact", stage=1, event_type="stage_completed")

        response = client.get(
            "/v1/v2/jobs/job-artifact/files/root-pom?stage=1&mode=download",
        )

        assert response.status_code == 200, response.text
        body = response.text
        # Download must NOT leak raw secrets
        assert "sk-secret789" not in body
        # Download must NOT leak absolute paths from content
        assert secret_path not in body
        # Download must still be valid XML with project element
        assert "<project>" in body or "<project " in body
        # Content-Disposition header should be set for download
        content_disp = response.headers.get("content-disposition", "")
        assert "attachment" in content_disp
        assert "stage-1-pom.xml" in content_disp


class TestQuestionDetection:
    """Test _question_looks_like_artifact_content heuristic."""

    def test_detects_artifact_content_questions(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _question_looks_like_artifact_content

        assert _question_looks_like_artifact_content("give me the full pom xml for stage 2")
        assert _question_looks_like_artifact_content("show me the openrewrite plugin")
        assert _question_looks_like_artifact_content("display the approved plan lock")
        assert _question_looks_like_artifact_content("preview the repair ledger")
        assert _question_looks_like_artifact_content("what is in the pom")
        assert _question_looks_like_artifact_content("show the rewrite dry run patch")

    def test_does_not_detect_non_artifact_questions(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _question_looks_like_artifact_content

        assert not _question_looks_like_artifact_content("what is the status?")
        assert not _question_looks_like_artifact_content("should I approve this?")
        assert not _question_looks_like_artifact_content("what failed?")
        assert not _question_looks_like_artifact_content("explain the pipeline")
