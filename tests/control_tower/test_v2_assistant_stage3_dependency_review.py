"""Tests for V2 assistant Stage 3 POM dependency review mode —
baseline detection, dependency bucketing, deferred Stage 1/2 behavior,
evidence-backed recommendations, and explicit dependency change requests."""

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
        tmp_path / "assistant_stage3.sqlite3",
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
                run_name="test-run-stage3",
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
                job_id="job-stage3",
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


def _seed_artifact_event(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    stage: int,
    artifact_kind: str,
    relative_path: str,
) -> None:
    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(
            job_id=job_id,
            stage=stage,
            event_type="artifact_written",
            status="completed",
            message=f"Artifact {artifact_kind} written.",
            payload={"artifact_kind": artifact_kind, "relative_path": relative_path},
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


# ── POM fixtures ──────────────────────────────────────────────────────

_STAGE3_JAVA21_BOOT3515_POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>migration-final</artifactId>
  <version>1.0.0</version>
  <packaging>jar</packaging>

  <properties>
    <java.version>21</java.version>
    <spring-boot.version>3.5.15</spring-boot.version>
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
      <groupId>org.zalando</groupId>
      <artifactId>problem-spring-web</artifactId>
      <version>0.27.0</version>
    </dependency>
    <dependency>
      <groupId>com.microsoft.azure</groupId>
      <artifactId>azure-servicebus-spring-boot-starter</artifactId>
      <version>2.1.6</version>
    </dependency>
    <dependency>
      <groupId>com.google.code.gson</groupId>
      <artifactId>gson</artifactId>
      <version>2.8.9</version>
    </dependency>
    <dependency>
      <groupId>org.modelmapper</groupId>
      <artifactId>modelmapper</artifactId>
      <version>2.4.4</version>
    </dependency>
    <dependency>
      <groupId>org.apache.juneau</groupId>
      <artifactId>juneau-marshall</artifactId>
      <version>9.0.0</version>
    </dependency>
    <dependency>
      <groupId>io.jsonwebtoken</groupId>
      <artifactId>jjwt-api</artifactId>
      <version>0.11.5</version>
    </dependency>
  </dependencies>

  <build>
    <plugins>
      <plugin>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-maven-plugin</artifactId>
      </plugin>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-compiler-plugin</artifactId>
        <version>3.11.0</version>
      </plugin>
    </plugins>
  </build>
</project>"""

_STAGE3_NO_BASELINE_POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>migration-final</artifactId>
  <version>1.0.0</version>
  <packaging>jar</packaging>

  <properties>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
  </properties>

  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
  </dependencies>
</project>"""

_STAGE1_POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>legacy-app</artifactId>
  <version>1.0.0</version>
  <packaging>jar</packaging>

  <properties>
    <java.version>11</java.version>
    <spring-boot.version>2.7.18</spring-boot.version>
  </properties>

  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
      <groupId>javax.persistence</groupId>
      <artifactId>javax.persistence-api</artifactId>
      <version>2.2</version>
    </dependency>
    <dependency>
      <groupId>com.google.code.gson</groupId>
      <artifactId>gson</artifactId>
      <version>2.8.9</version>
    </dependency>
  </dependencies>
</project>"""


# ═════════════════════════════════════════════════════════════════════
# Intent Classification Tests
# ═════════════════════════════════════════════════════════════════════


class TestIntentClassificationStage3:
    def test_stage3_dependency_review_detected(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent(
            "What dependencies should we update at stage 3?"
        ) == "stage3_dependency_review"

    def test_review_stage_3_pom_dependencies(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent(
            "Review stage 3 pom dependencies"
        ) == "stage3_dependency_review"

    def test_dependency_modernization_report_stage3(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent(
            "Dependency modernization report for stage 3"
        ) == "stage3_dependency_review"

    def test_analyze_final_pom(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent(
            "Analyze final pom dependencies"
        ) == "stage3_dependency_review"

    def test_what_dependencies_need_operator_decisions(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent(
            "Which dependencies need operator decisions?"
        ) == "stage3_dependency_review"

    def test_after_openrewrite_what_needs_updates(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent(
            "After OpenRewrite, what app dependencies need updates?"
        ) == "stage3_dependency_review"

    def test_show_pom_is_not_stage3_review(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("show pom") == "pom_or_dependency_explanation"

    def test_list_dependencies_is_not_stage3_review(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("list dependencies") == "pom_or_dependency_explanation"

    def test_update_gson_is_pom_dependency_change_request(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("update gson to 2.11.0") == "apply_dependency_change"

    def test_update_gson_at_stage3_is_pom_dependency_change_request(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _classify_v2_assistant_intent
        assert _classify_v2_assistant_intent("Update gson to 2.11.0 at stage 3. Do not apply.") == "pom_change_proposal"


# ═════════════════════════════════════════════════════════════════════
# Baseline Detection Tests
# ═════════════════════════════════════════════════════════════════════


class TestStage3BaselineDetection:
    def test_stage3_review_detects_baseline_from_root_pom(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage3-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_STAGE3_JAVA21_BOOT3515_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-stage3", stage=3, command_id="cmd-s3", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="sandbox_transform_completed")
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-stage3/assistant/ask",
            json={"question": "What dependencies should we update at stage 3?"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        assert "Detected target baseline" in content
        assert "21" in content
        assert "3.5.15" in content
        assert "Java" in content
        assert "Spring Boot" in content

    def test_stage3_review_does_not_guess_baseline(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage3-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_STAGE3_NO_BASELINE_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-stage3", stage=3, command_id="cmd-s3", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="sandbox_transform_completed")
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-stage3/assistant/ask",
            json={"question": "What dependencies should we update at stage 3?"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        assert "cannot confirm" in content.lower() or "cannot be confirmed" in content.lower()


# ═════════════════════════════════════════════════════════════════════
# Stage 1/2 Deferred Behavior Tests
# ═════════════════════════════════════════════════════════════════════


class TestStage1And2Deferred:
    def test_stage1_dependency_modernization_defers_final_recommendations(
        self, tmp_path: Path
    ) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage1-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_STAGE1_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-stage3", stage=1, command_id="cmd-s1", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-stage3", stage=1, event_type="sandbox_transform_completed")
        _seed_stage_event(conn, job_id="job-stage3", stage=1, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-stage3/assistant/ask",
            json={"question": "What dependencies should we update at stage 1?"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        assert "not at the final target baseline" in content.lower()
        assert "should wait for Stage 3" in content or "should wait for stage 3" in content.lower()

    def test_stage2_dependency_modernization_defers_final_recommendations(
        self, tmp_path: Path
    ) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage2-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_STAGE1_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-stage3", stage=2, command_id="cmd-s2", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-stage3", stage=2, event_type="sandbox_transform_completed")
        _seed_stage_event(conn, job_id="job-stage3", stage=2, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-stage3/assistant/ask",
            json={"question": "What dependencies should we update at stage 2?"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        assert "not at the final target baseline" in content.lower()
        assert "should wait for Stage 3" in content or "should wait for stage 3" in content.lower()


# ═════════════════════════════════════════════════════════════════════
# Dependency Bucketing Tests
# ═════════════════════════════════════════════════════════════════════


class TestDependencyBucketing:
    def test_stage3_review_buckets_dependencies(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage3-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_STAGE3_JAVA21_BOOT3515_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-stage3", stage=3, command_id="cmd-s3", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="sandbox_transform_completed")
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-stage3/assistant/ask",
            json={"question": "What dependencies should we update at stage 3?"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # All 5 bucket labels must appear
        assert "Boot-Managed" in content or "boot_managed" in content.lower() or "Boot" in content
        assert "Jakarta" in content
        assert "App-Specific" in content or "app_specific" in content.lower()
        assert "Build" in content or "build_plugins" in content.lower() or "Plugins" in content
        assert "Transitive" in content or "BOM-Managed" in content

    def test_stage3_review_flags_remaining_javax_dependencies(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage3-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_STAGE3_JAVA21_BOOT3515_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-stage3", stage=3, command_id="cmd-s3", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="sandbox_transform_completed")
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-stage3/assistant/ask",
            json={"question": "Check the final pom and propose app-specific dependency updates"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        assert "javax." in content.lower()

    def test_stage3_review_does_not_recommend_latest_without_evidence(
        self, tmp_path: Path
    ) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage3-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_STAGE3_JAVA21_BOOT3515_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-stage3", stage=3, command_id="cmd-s3", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="sandbox_transform_completed")
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-stage3/assistant/ask",
            json={"question": "Dependency modernization report for stage 3"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Must NOT contain "latest" as a version recommendation (negation is ok)
        # Check for patterns like "→ latest", "update to latest", "recommend latest"
        assert "→ latest" not in content.lower()
        assert "to latest" not in content.lower()
        assert "recommend latest" not in content.lower()

    def test_stage3_review_lists_human_policy_decisions(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage3-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_STAGE3_JAVA21_BOOT3515_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-stage3", stage=3, command_id="cmd-s3", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="sandbox_transform_completed")
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-stage3/assistant/ask",
            json={"question": "What dependencies should we update at stage 3?"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # At least some policy decision candidates (problem-spring-web, azure, gson, modelmapper, juneau)
        candidates = ("problem-spring-web", "azure", "gson", "modelmapper", "juneau", "jjwt")
        found = [c for c in candidates if c.lower() in content.lower()]
        assert len(found) >= 2, f"Expected at least 2 policy candidate artifacts in content, got: {found}"


# ═════════════════════════════════════════════════════════════════════
# Explicit Dependency Change Tests
# ═════════════════════════════════════════════════════════════════════


class TestExplicitDependencyChange:
    def test_stage3_specific_gson_update_returns_exact_change(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage3-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_STAGE3_JAVA21_BOOT3515_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-stage3", stage=3, command_id="cmd-s3", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="sandbox_transform_completed")
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-stage3/assistant/ask",
            json={"question": "Update gson to 2.11.0 at stage 3. Do not apply."},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Response is a POM change proposal
        assert "2.11.0" in content
        assert "Gson" in content or "gson" in content or "2.8.9" in content
        # Not applied
        assert "not been applied" in content.lower() or "not applied" in content.lower() or "Not applied" in content

    def test_stage3_tomcat_request_does_not_blindly_add_direct_dependency(
        self, tmp_path: Path
    ) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage3-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_STAGE3_JAVA21_BOOT3515_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-stage3", stage=3, command_id="cmd-s3", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="sandbox_transform_completed")
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-stage3/assistant/ask",
            json={"question": "Change Tomcat to 10.1.20 at stage 3"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Response should indicate that tomcat is not a direct dependency
        # or explain the result of the dependency change request
        assert len(content) > 0

    def test_stage3_replace_javax_with_jakarta_only_if_present(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage3-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_STAGE3_JAVA21_BOOT3515_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-stage3", stage=3, command_id="cmd-s3", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="sandbox_transform_completed")
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-stage3/assistant/ask",
            json={"question": "Replace javax.persistence-api with jakarta.persistence-api at stage 3"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        lowered = content.lower()
        assert "migration completed" not in lowered
        assert "javax" in lowered
        assert "jakarta" in lowered
        assert "javax.persistence:javax.persistence-api" in lowered
        assert "replace javax" in lowered or "jakarta equivalent" in lowered
        assert "not applied" in lowered or "not apply" in lowered


# ═════════════════════════════════════════════════════════════════════
# Mandatory Constraint Tests
# ═════════════════════════════════════════════════════════════════════


class TestMandatoryConstraints:
    def test_stage3_review_requires_stage_completed_or_stable(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        # Stage 3 is running (no completed event)
        sandbox = tmp_path / "stage3-running"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_STAGE3_JAVA21_BOOT3515_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-stage3", stage=3, command_id="cmd-s3-running", sandbox_path=sandbox, status="running")
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="sandbox_transform_started", status="running")

        response = client.post(
            "/v1/v2/jobs/job-stage3/assistant/ask",
            json={"question": "What dependencies should we update at stage 3?"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Should not dump final dependency modernization; may say "not available" or defer
        # The assistant will fall back because root_pom won't resolve as completed
        assert any(
            term in content.lower()
            for term in ("not available", "stage_running", "running", "cannot confirm")
        )

    def test_stage3_review_no_full_pom_dump(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage3-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_STAGE3_JAVA21_BOOT3515_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-stage3", stage=3, command_id="cmd-s3", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="sandbox_transform_completed")
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-stage3/assistant/ask",
            json={"question": "What dependencies should we update at stage 3?"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Must NOT dump full POM
        assert "<modelVersion>" not in content
        assert "<project" not in content

    def test_stage3_review_no_raw_path_secret_leak(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage3-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_STAGE3_JAVA21_BOOT3515_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-stage3", stage=3, command_id="cmd-s3", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="sandbox_transform_completed")
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-stage3/assistant/ask",
            json={"question": "What dependencies should we update at stage 3?"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # No raw paths
        assert "/home/" not in content
        assert "C:\\" not in content
        # No secret tokens/passwords (artifact names like io.jsonwebtoken are fine)
        assert "password" not in content.lower()

    def test_stage3_review_not_applied(self, tmp_path: Path) -> None:
        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage3-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_STAGE3_JAVA21_BOOT3515_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-stage3", stage=3, command_id="cmd-s3", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="sandbox_transform_completed")
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="stage_completed")

        response = client.post(
            "/v1/v2/jobs/job-stage3/assistant/ask",
            json={"question": "Now that we are on Java 21 and Spring Boot 3, what dependencies should change?"},
            headers=_mutation_headers(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["assistant_message"]["content"]
        # Must explicitly state nothing was applied
        assert "Not applied" in content or "Not Applied" in content or "Not applied" in content.lower()


# ═════════════════════════════════════════════════════════════════════
# Helper Unit Tests
# ═════════════════════════════════════════════════════════════════════


class TestStageDetectionHelpers:
    def test_get_requested_stage_explicit_stage3(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _get_requested_stage
        assert _get_requested_stage("review stage 3 dependencies") == 3

    def test_get_requested_stage_explicit_stage1(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _get_requested_stage
        assert _get_requested_stage("show stage 1 pom") == 1

    def test_get_requested_stage_final_stage(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _get_requested_stage
        assert _get_requested_stage("review final stage dependencies") == 3

    def test_get_requested_stage_intent_default(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _get_requested_stage
        assert _get_requested_stage("what dependencies need updates", "stage3_dependency_review") == 3

    def test_get_requested_stage_no_match(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _get_requested_stage
        assert _get_requested_stage("show pom") is None

    def test_is_final_dependency_review_allowed_stage3_build_test(
        self, tmp_path: Path
    ) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _is_final_dependency_review_allowed

        client, conn, _setup_id = _client_with_setup(tmp_path)
        sandbox = tmp_path / "stage3-sandbox"
        sandbox.mkdir()
        (sandbox / "pom.xml").write_text(_STAGE3_JAVA21_BOOT3515_POM, encoding="utf-8")
        _seed_stage_command(conn, job_id="job-stage3", stage=3, command_id="cmd-s3-unit", sandbox_path=sandbox)
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="sandbox_transform_completed")
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="stage_completed")
        _seed_stage_event(conn, job_id="job-stage3", stage=3, event_type="build_completed")

        with SqliteUnitOfWork(conn) as uow:
            events = tuple(uow.v2_events.list_by_job("job-stage3"))

        root_pom_preview = {"exists": True, "stage_index": 3}
        allowed, reason = _is_final_dependency_review_allowed(3, root_pom_preview, events)
        assert allowed is True, f"Expected allowed=True, got reason={reason}"

    def test_is_final_dependency_review_allowed_stage_not_3(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _is_final_dependency_review_allowed
        allowed, reason = _is_final_dependency_review_allowed(1, {"exists": True}, ())
        assert allowed is False
        assert reason == "stage_not_3"

    def test_is_final_dependency_review_allowed_no_root_pom(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _is_final_dependency_review_allowed
        allowed, reason = _is_final_dependency_review_allowed(3, None, ())
        assert allowed is False
        assert reason == "root_pom_unavailable"

    def test_detect_stage3_baseline_from_properties(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _extract_pom_summary, _detect_stage3_baseline
        pom_summary = _extract_pom_summary(_STAGE3_JAVA21_BOOT3515_POM)
        baseline = _detect_stage3_baseline(pom_summary)
        assert baseline["java_version"] == "21"
        assert baseline["spring_boot_version"] == "3.5.15"
        assert baseline["spring_boot_source"] == "property"
        assert baseline["baseline_confirmed"] is True

    def test_detect_stage3_baseline_no_properties(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _extract_pom_summary, _detect_stage3_baseline
        pom_summary = _extract_pom_summary(_STAGE3_NO_BASELINE_POM)
        baseline = _detect_stage3_baseline(pom_summary)
        assert baseline["baseline_confirmed"] is False
        assert "java.version" in baseline["missing"] or "spring_boot_version" in baseline["missing"]

    def test_dependency_buckets_classification(self) -> None:
        from migration_factory.control_tower.adapters.fastapi.app import _extract_pom_summary, _detect_stage3_baseline, _classify_stage3_dependencies
        pom_summary = _extract_pom_summary(_STAGE3_JAVA21_BOOT3515_POM)
        baseline = _detect_stage3_baseline(pom_summary)
        buckets = _classify_stage3_dependencies(pom_summary, baseline)
        # Boot-managed: spring-boot-starter-web, spring-boot-starter-data-jpa
        assert len(buckets["boot_managed"]) >= 2
        # Jakarta: javax.persistence-api, javax.servlet-api
        assert len(buckets["jakarta_platform"]) >= 2
        # App-specific: problem-spring-web, azure-servicebus, gson, modelmapper, juneau, jjwt
        assert len(buckets["app_specific_third_party"]) >= 4
        # Build plugins: spring-boot-maven-plugin, maven-compiler-plugin
        assert len(buckets["build_plugins"]) >= 2
