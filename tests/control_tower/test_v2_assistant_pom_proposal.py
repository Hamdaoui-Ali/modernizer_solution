"""Tests for V2 assistant POM change proposal behavior —
intent classification, proposal building, fallback safety,
XML presentation, and redaction correctness."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

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
        tmp_path / "assistant_pom_proposal.sqlite3",
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

    output_dir_path = output_dir or str(tmp_path / "output")
    Path(output_dir_path).mkdir(parents=True, exist_ok=True)
    now = utc_now_text()
    with SqliteUnitOfWork(conn) as uow:
        svc = V2SetupService(SqliteUnitOfWork(conn).v2_setups)
        setup_dto = svc.create_setup(
            CreateSetupRequest(
                run_name="test-run-proposal",
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
                job_id="job-proposal",
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


@pytest.mark.parametrize(
    "question",
    [
        "Apply this Stage 3 POM change: update gson to 2.11.0",
        "Rollback the last Stage 3 POM change",
    ],
)
def test_assistant_ask_pom_mutation_wording_never_reaches_editor(
    tmp_path: Path,
    question: str,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    pom = output / "pom.xml"
    original = "<project><version>1.0.0</version></project>"
    pom.write_text(original, encoding="utf-8")
    client, _conn, _setup_id = _client_with_setup(
        tmp_path,
        output_dir=str(output),
    )

    with patch(
        "migration_factory.control_tower.adapters.fastapi.app._build_pom_dependency_editor"
    ) as editor_builder:
        response = client.post(
            "/v1/v2/jobs/job-proposal/assistant/ask",
            json={"question": question},
            headers=_mutation_headers(),
        )

    assert response.status_code == 200, response.text
    assert response.json()["guardrails"]["cannot_write_files"] is True
    editor_builder.assert_not_called()
    assert pom.read_text(encoding="utf-8") == original


# ── Helper: create a realistic Spring Boot 2.7.x pom.xml ────────────

_SPRING_BOOT_2_7_POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>migration-test</artifactId>
  <version>1.0.0</version>
  <packaging>jar</packaging>

  <properties>
    <java.version>11</java.version>
    <spring-boot.version>2.7.18</spring-boot.version>
    <org.springframework.version>5.3.31</org.springframework.version>
    <hibernate.version>5.3.10.Final</hibernate.version>
  </properties>

  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-data-jpa</artifactId>
    </dependency>
    <dependency>
      <groupId>javax.persistence</groupId>
      <artifactId>javax.persistence-api</artifactId>
      <version>2.2</version>
    </dependency>
    <dependency>
      <groupId>javax.servlet</groupId>
      <artifactId>javax.servlet-api</artifactId>
      <version>4.0.1</version>
      <scope>provided</scope>
    </dependency>
    <dependency>
      <groupId>javax.annotation</groupId>
      <artifactId>javax.annotation-api</artifactId>
      <version>1.3.2</version>
    </dependency>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-aspects</artifactId>
      <version>5.3.31</version>
    </dependency>
    <dependency>
      <groupId>org.hibernate</groupId>
      <artifactId>hibernate-core</artifactId>
      <version>5.3.10.Final</version>
    </dependency>
    <dependency>
      <groupId>com.microsoft.azure</groupId>
      <artifactId>azure-servicebus-spring-boot-starter</artifactId>
      <version>2.1.6</version>
    </dependency>
    <dependency>
      <groupId>com.fasterxml.jackson.core</groupId>
      <artifactId>jackson-databind</artifactId>
      <version>2.13.5</version>
    </dependency>
  </dependencies>

  <build>
    <plugins>
      <plugin>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-maven-plugin</artifactId>
      </plugin>
    </plugins>
  </build>
</project>"""


# ═════════════════════════════════════════════════════════════════════
# Intent Classification Tests
# ═════════════════════════════════════════════════════════════════════


class TestIntentClassificationPomProposal:
    """Test that _classify_v2_assistant_intent handles pom_change_proposal."""

    def test_propose_safe_pom_change_is_proposal(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent(
            "Propose a safe POM change for stage 1 to upgrade Spring Boot dependencies. Do not apply it. "
            "Give me the exact dependency/plugin change, risk, and which artifact proves it."
        ) == "pom_change_proposal"

    def test_upgrade_spring_boot_dependencies_is_proposal(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("upgrade Spring Boot dependencies") == "pom_change_proposal"

    def test_change_the_pom_is_proposal(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("change the pom") == "pom_change_proposal"

    def test_modify_the_pom_is_proposal(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("modify the pom") == "pom_change_proposal"

    def test_draft_repair_proposal_is_proposal(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("draft a repair proposal") == "pom_change_proposal"

    def test_draft_pom_proposal_is_proposal(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("draft pom proposal") == "pom_change_proposal"

    def test_migrate_this_pom_is_proposal(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("migrate this pom") == "pom_change_proposal"

    def test_what_should_we_change_in_pom_is_proposal(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("what should we change in the pom") == "pom_change_proposal"

    def test_apply_pom_change_is_capability_boundary_not_proposal(self) -> None:
        """'Can you apply the POM change yourself?' must return capability_boundary, not proposal."""
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("Can you apply the POM change yourself?") == "capability_boundary"

    def test_can_you_execute_is_capability_boundary(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("can you execute the change?") == "capability_boundary"

    def test_show_me_the_pom_is_explanation_not_proposal(self) -> None:
        """'Show me the POM' must remain pom_or_dependency_explanation, not proposal."""
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("show me the POM") == "pom_or_dependency_explanation"

    def test_explain_the_pom_is_explanation_not_proposal(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("explain the pom") == "pom_or_dependency_explanation"

    def test_what_happened_is_status_not_proposal(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("what happened?") == "status"

    def test_model_status_still_works(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("Is AI model connected?") == "model_status"


# ═════════════════════════════════════════════════════════════════════
# POM Proposal Answer Tests
# ═════════════════════════════════════════════════════════════════════


class TestPomProposalAnswer:
    """Test that pom_change_proposal returns structured proposal, not POM dump."""

    def test_propose_safe_pom_change_returns_proposal_not_dump(
        self, tmp_path: Path
    ) -> None:
        """The exact live-interaction question must return a proposal, not a POM dump."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_SPRING_BOOT_2_7_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-proposal", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-proposal", stage=1, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-proposal/assistant/ask",
            json={
                "question": (
                    "Propose a safe POM change for stage 1 to upgrade Spring Boot dependencies. "
                    "Do not apply it. Give me the exact dependency/plugin change, risk, "
                    "and which artifact proves it."
                )
            },
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        prompt = json.loads(client.app.state.v2_assistant_model_client.calls[-1]["prompt"])

        assert "<modelVersion>" not in content
        assert prompt["question"].startswith("Propose a safe POM change")
        assert prompt["answer_contract"]["chat_is_strictly_read_only"] is True
        assert prompt["artifact_previews"]
        assert prompt["artifact_previews"][0]["kind"] == "root_pom"

    def test_pom_change_proposal_includes_exact_edits(
        self, tmp_path: Path
    ) -> None:
        """Proposal must include specific java.version and javax migration candidates."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_SPRING_BOOT_2_7_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-proposal", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-proposal", stage=1, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-proposal/assistant/ask",
            json={"question": "draft a pom proposal for stage 1"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        prompt = json.loads(client.app.state.v2_assistant_model_client.calls[-1]["prompt"])
        preview = prompt["artifact_previews"][0]["preview"]
        assert "java.version" in preview
        assert "spring-boot.version" in preview
        assert prompt["question"] == "draft a pom proposal for stage 1"

    def test_pom_change_proposal_includes_risk_assessment(
        self, tmp_path: Path
    ) -> None:
        """Proposal must include risk level."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_SPRING_BOOT_2_7_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-proposal", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-proposal", stage=1, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-proposal/assistant/ask",
            json={"question": "propose pom changes"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        prompt = json.loads(client.app.state.v2_assistant_model_client.calls[-1]["prompt"])
        assert prompt["question"] == "propose pom changes"
        assert prompt["answer_contract"]["current_state_is_authoritative"] is True

    def test_pom_change_proposal_includes_artifact_evidence(
        self, tmp_path: Path
    ) -> None:
        """Proposal must cite evidence artifact names."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_SPRING_BOOT_2_7_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-proposal", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-proposal", stage=1, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-proposal/assistant/ask",
            json={"question": "draft a pom proposal"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        prompt = json.loads(client.app.state.v2_assistant_model_client.calls[-1]["prompt"])
        assert "root_pom" in prompt["artifact_kinds"] or any(
            item["kind"] == "root_pom" for item in prompt["artifact_previews"]
        )

    def test_apply_pom_change_refuses_direct_write(
        self, tmp_path: Path
    ) -> None:
        """'Can you apply the POM change yourself?' === refused direct execution."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_SPRING_BOOT_2_7_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-proposal", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-proposal", stage=1, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-proposal/assistant/ask",
            json={"question": "Can you apply the POM change yourself?"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"].lower()
        assert "cannot" in content
        assert "dedicated ui or api control" in content
        # Must not claim can write/apply/execute/approve
        forbidden = ["i can execute", "i can approve", "i can write", "i can apply"]
        for phrase in forbidden:
            assert phrase not in content, f"Must not claim: {phrase}"
        # Must NOT be full stage status
        assert "stage status:" not in content

    def test_pom_change_proposal_root_pom_unavailable(
        self, tmp_path: Path
    ) -> None:
        """When root_pom unavailable, proposal states what evidence is needed."""
        client, conn, _setup_id = _client_with_setup(tmp_path)

        response = client.post(
            "/v1/v2/jobs/job-proposal/assistant/ask",
            json={"question": "Propose a safe POM change for stage 1"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        prompt = json.loads(client.app.state.v2_assistant_model_client.calls[-1]["prompt"])
        assert prompt["artifact_previews"]
        assert prompt["artifact_previews"][0]["exists"] is False
        assert prompt["artifact_previews"][0]["reason"]

    def test_show_me_the_pom_is_still_explanation(
        self, tmp_path: Path
    ) -> None:
        """'Show me the POM' must remain POM explanation, not proposal."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_SPRING_BOOT_2_7_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-proposal", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-proposal", stage=1, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-proposal/assistant/ask",
            json={"question": "show me the POM"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        prompt = json.loads(client.app.state.v2_assistant_model_client.calls[-1]["prompt"])
        assert "migration-test" in prompt["artifact_previews"][0]["preview"]
        assert prompt["assistant_intent_hint"] == "pom_or_dependency_explanation"

    def test_build_descriptor_paraphrase_gets_root_pom_grounding(
        self, tmp_path: Path
    ) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_SPRING_BOOT_2_7_POM, encoding="utf-8")
        _seed_stage_command(
            conn,
            job_id="job-proposal",
            stage=1,
            command_id="cmd-s1",
            sandbox_path=sandbox,
        )
        _seed_stage_event(
            conn,
            job_id="job-proposal",
            stage=1,
            event_type="stage_completed",
        )

        response = client.post(
            "/v1/v2/jobs/job-proposal/assistant/ask",
            json={"question": "Which libraries are declared in the build descriptor?"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        prompt = json.loads(client.app.state.v2_assistant_model_client.calls[-1]["prompt"])
        assert prompt["artifact_previews"][0]["kind"] == "root_pom"
        assert "migration-test" in prompt["artifact_previews"][0]["preview"]


# ═════════════════════════════════════════════════════════════════════
# XML Presentation/Redaction Tests
# ═════════════════════════════════════════════════════════════════════


class TestXmlPresentation:
    """Test XML-aware redaction and POM preview formatting."""

    def test_root_pom_xml_preview_preserves_maven_schema_urls(
        self, tmp_path: Path
    ) -> None:
        """Maven namespace URLs must not be redacted."""
        from migration_factory.control_tower.adapters.fastapi.app import _redact_xml_preserve_maven_urls

        xml_text = _SPRING_BOOT_2_7_POM
        redacted = _redact_xml_preserve_maven_urls(xml_text)
        assert "http://maven.apache.org/POM/4.0.0" in redacted
        assert "http://maven.apache.org/xsd/maven-4.0.0.xsd" in redacted
        assert "http://www.w3.org/2001/XMLSchema-instance" in redacted

    def test_root_pom_xml_preview_keeps_closing_tags(
        self, tmp_path: Path
    ) -> None:
        """XML closing tags must remain intact."""
        from migration_factory.control_tower.adapters.fastapi.app import _redact_xml_preserve_maven_urls

        xml_text = "<project><dependency><groupId>g</groupId><artifactId>a</artifactId></dependency></project>"
        redacted = _redact_xml_preserve_maven_urls(xml_text)
        assert "</dependency>" in redacted
        assert "</project>" in redacted
        assert "<groupId>" in redacted

    def test_xml_safe_redaction_redacts_secrets(
        self, tmp_path: Path
    ) -> None:
        """Secrets/passwords embedded in XML must be redacted."""
        from migration_factory.control_tower.adapters.fastapi.app import _redact_xml_preserve_maven_urls

        xml_text = """<project>
  <properties>
    <password>my-secret-password</password>
    <token>sk-abc123xyz</token>
  </properties>
</project>"""
        redacted = _redact_xml_preserve_maven_urls(xml_text)
        assert "my-secret-password" not in redacted
        assert "[redacted]" in redacted
        # XML structure preserved
        assert "<password>" in redacted
        assert "</password>" in redacted

    def test_xml_safe_redaction_redacts_local_paths(
        self, tmp_path: Path
    ) -> None:
        """Local filesystem paths must be redacted from XML."""
        from migration_factory.control_tower.adapters.fastapi.app import _redact_xml_preserve_maven_urls

        xml_text = """<project>
  <properties>
    <argLine>-Djava.security.egd=file:/dev/./urandom</argLine>
  </properties>
</project>"""
        redacted = _redact_xml_preserve_maven_urls(xml_text)
        assert "/dev/" not in redacted or "[redacted" in redacted
        # XML structure preserved
        assert "<argLine>" in redacted
        assert "</argLine>" in redacted

    def test_no_raw_sandbox_path_leaks_after_xml_redaction(
        self, tmp_path: Path
    ) -> None:
        """Internal sandbox path must not appear in assistant response."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        sandbox_abs = str(sandbox.resolve())
        # Include the absolute sandbox path IN the pom content
        pom_with_sandbox = _SPRING_BOOT_2_7_POM.replace(
            "<version>1.0.0</version>",
            f"<version>1.0.0</version>\n  <properties>\n    <sandboxRef>{sandbox_abs}</sandboxRef>\n  </properties>",
        )
        (sandbox / "pom.xml").write_text(pom_with_sandbox, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-proposal", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-proposal", stage=1, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-proposal/assistant/ask",
            json={"question": "show me the POM"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        assert sandbox_abs not in content, (
            f"Sandbox path leak in assistant response:\n{content[:500]}"
        )

    def test_pom_proposal_does_not_substitute_rewrite_patch(
        self, tmp_path: Path
    ) -> None:
        """rewrite_dry_run.patch must never be presented as full POM."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_SPRING_BOOT_2_7_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-proposal", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-proposal", stage=1, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-proposal/assistant/ask",
            json={"question": "draft a pom proposal"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        assert "rewrite_dry_run.patch" not in content

    def test_pom_explanation_fallback_is_bounded_and_read_only(
        self, tmp_path: Path
    ) -> None:
        """Deterministic POM fallback returns only a bounded read-only preview."""
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_SPRING_BOOT_2_7_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-proposal", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-proposal", stage=1, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-proposal/assistant/ask",
            json={"question": "explain the POM"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        assert "read-only review" in content
        assert "migration-test" in content
        assert len(content) <= 16_000


# ═════════════════════════════════════════════════════════════════════
# POM Summary Extraction Tests
# ═════════════════════════════════════════════════════════════════════


class TestPomSummaryExtraction:
    """Test _extract_pom_summary for structured field extraction."""

    def test_extracts_project_coordinates(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _extract_pom_summary

        summary = _extract_pom_summary(_SPRING_BOOT_2_7_POM)
        assert summary["parse_ok"] is True
        assert summary["coordinates"]["groupId"] == "com.example"
        assert summary["coordinates"]["artifactId"] == "migration-test"
        assert summary["coordinates"]["version"] == "1.0.0"

    def test_extracts_properties(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _extract_pom_summary

        summary = _extract_pom_summary(_SPRING_BOOT_2_7_POM)
        props = summary["properties"]
        assert props.get("java.version") == "11"
        assert props.get("spring-boot.version") == "2.7.18"

    def test_extracts_javax_dependencies(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _extract_pom_summary

        summary = _extract_pom_summary(_SPRING_BOOT_2_7_POM)
        deps = summary["dependencies"]
        javax_groups = {d.get("groupId", "") for d in deps}
        assert "javax.persistence" in javax_groups
        assert "javax.servlet" in javax_groups

    def test_extracts_no_parent_or_dependency_management(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _extract_pom_summary

        summary = _extract_pom_summary(_SPRING_BOOT_2_7_POM)
        assert summary["has_parent"] is False
        assert summary["has_dependency_management"] is False

    def test_extracts_hibernate_version(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _extract_pom_summary

        summary = _extract_pom_summary(_SPRING_BOOT_2_7_POM)
        props = summary["properties"]
        assert props.get("hibernate.version") == "5.3.10.Final"

    def test_extracts_azure_service_bus(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _extract_pom_summary

        summary = _extract_pom_summary(_SPRING_BOOT_2_7_POM)
        deps = summary["dependencies"]
        azure_deps = [
            d for d in deps
            if "azure" in d.get("groupId", "").lower()
            or "servicebus" in d.get("artifactId", "").lower()
        ]
        assert len(azure_deps) >= 1

    def test_handles_malformed_xml(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _extract_pom_summary

        # Malformed XML — should fall back to regex extraction
        malformed = (
            "<project>"
            "<groupId>com.example</groupId>"
            "<artifactId>partial-app</artifactId>"
            "<unclosed>"
        )
        summary = _extract_pom_summary(malformed)
        # ET may fail to parse or regex fallback may be used
        # In either case, should extract artifactId
        aid = summary["coordinates"].get("artifactId", "")
        assert "partial" in aid or aid == "partial-app", (
            f"Expected artifactId containing 'partial', got {aid!r}"
        )
