from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.application.target_version_update import (
    PomTargetVersionChange,
    apply_target_version_updates,
)


def test_apply_target_versions_updates_direct_and_property_versions() -> None:
    pom = """
<project>
  <properties>
    <jackson.version>2.15.0</jackson.version>
  </properties>
  <dependencies>
    <dependency>
      <groupId>com.fasterxml.jackson.core</groupId>
      <artifactId>jackson-databind</artifactId>
      <version>${jackson.version}</version>
    </dependency>
    <dependency>
      <groupId>com.google.code.gson</groupId>
      <artifactId>gson</artifactId>
      <version>2.9.0</version>
    </dependency>
  </dependencies>
</project>
""".strip()

    result = apply_target_version_updates(pom, [
        PomTargetVersionChange("com.fasterxml.jackson.core", "jackson-databind", "2.17.2"),
        PomTargetVersionChange("com.google.code.gson", "gson", "2.11.0"),
    ])

    assert result["applied_count"] == 2
    assert result["blocked_count"] == 0
    assert "<jackson.version>2.17.2</jackson.version>" in result["pom_content"]
    assert "<version>2.11.0</version>" in result["pom_content"]
    assert result["before_checksum"] != result["after_checksum"]


def test_apply_target_versions_skips_missing_and_versionless_dependencies() -> None:
    pom = """
<project>
  <dependencies>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
    </dependency>
  </dependencies>
</project>
""".strip()

    result = apply_target_version_updates(pom, [
        PomTargetVersionChange("org.junit.jupiter", "junit-jupiter", "5.11.4"),
        PomTargetVersionChange("com.example", "missing", "1.0.0"),
    ])

    assert result["applied_count"] == 0
    assert result["skipped_count"] == 2
    assert [item["reason"] for item in result["items"]] == [
        "dependency has no explicit version to update",
        "dependency not found in pom.xml",
    ]
    assert result["pom_content"] == pom


def test_apply_target_versions_blocks_duplicate_pom_entries_and_parent_updates() -> None:
    pom = """
<project>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.5.0</version>
  </parent>
  <dependencies>
    <dependency>
      <groupId>com.example</groupId>
      <artifactId>library</artifactId>
      <version>1.0.0</version>
    </dependency>
    <dependency>
      <groupId>com.example</groupId>
      <artifactId>library</artifactId>
      <version>1.1.0</version>
    </dependency>
  </dependencies>
</project>
""".strip()

    result = apply_target_version_updates(pom, [
        PomTargetVersionChange("com.example", "library", "2.0.0"),
        PomTargetVersionChange("org.springframework.boot", "spring-boot-starter-parent", "4.0.0"),
    ])

    assert result["applied_count"] == 0
    assert result["blocked_count"] == 2
    assert "duplicate dependency entries" in result["items"][0]["reason"]
    assert "parent version updates" in result["items"][1]["reason"]
    assert result["pom_content"] == pom


def test_apply_target_versions_rejects_malformed_xml_without_changes() -> None:
    pom = "<project><dependencies></project>"

    result = apply_target_version_updates(pom, [
        PomTargetVersionChange("com.example", "library", "1.0.0"),
    ])

    assert result["applied_count"] == 0
    assert result["blockers"] == ["pom.xml is malformed; no changes applied"]
    assert result["pom_content"] == pom


def test_apply_target_versions_blocks_invalid_requested_versions() -> None:
    pom = """
<project>
  <dependencies>
    <dependency>
      <groupId>com.example</groupId>
      <artifactId>library</artifactId>
      <version>1.0.0</version>
    </dependency>
  </dependencies>
</project>
""".strip()

    result = apply_target_version_updates(pom, [
        PomTargetVersionChange("com.example", "library", "1.0.0 <bad>"),
    ])

    assert result["applied_count"] == 0
    assert result["blocked_count"] == 1
    assert result["items"][0]["reason"] == "invalid target version"
    assert result["pom_content"] == pom

def test_apply_target_versions_endpoint_updates_requested_stage_backend_pom(tmp_path: Path) -> None:
    client, conn = _client_with_job(tmp_path)
    sandbox = tmp_path / "stage3-sandbox"
    sandbox.mkdir()
    pom_path = sandbox / "pom.xml"
    pom_path.write_text(
        """
<project>
  <dependencies>
    <dependency>
      <groupId>com.example</groupId>
      <artifactId>library</artifactId>
      <version>1.0.0</version>
    </dependency>
  </dependencies>
</project>
""".strip(),
        encoding="utf-8",
    )
    _seed_stage_command(conn, stage=3, command_id="cmd-s3", sandbox_path=sandbox)
    _seed_stage_event(conn, stage=3, event_type="stage_completed")

    response = client.post(
        "/v1/v2/jobs/job-stage4/stage/3/pom/apply-target-version-changes",
        headers=_mutation_headers(),
        json={
            "idempotency_key": "csv-update-1",
            "changes": [
                {"group_id": "com.example", "artifact_id": "library", "target_version": "2.0.0"},
            ],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied_count"] == 1
    assert body["idempotency_key"] == "csv-update-1"
    assert "pom_content" not in body
    assert "path" not in json.dumps(body).lower()
    assert "<version>2.0.0</version>" in pom_path.read_text(encoding="utf-8")


def _mutation_headers() -> dict[str, str]:
    from migration_factory.control_tower.adapters.fastapi.security import DEFAULT_FRONTEND_CLIENT_ID

    return {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": DEFAULT_FRONTEND_CLIENT_ID,
    }


def _client_with_job(tmp_path: Path) -> tuple[TestClient, sqlite3.Connection]:
    from migration_factory.control_tower.adapters.fastapi import create_app
    from migration_factory.control_tower.application.v2_setup_service import CreateSetupRequest, V2SetupService
    from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import V2MigrationJobRecord

    conn = sqlite3.connect(
        tmp_path / "stage4_target_versions.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_pending_migrations(conn)
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    now = utc_now_text()
    with SqliteUnitOfWork(conn) as uow:
        setup = V2SetupService(SqliteUnitOfWork(conn).v2_setups).create_setup(
            CreateSetupRequest(
                run_name="stage4-target-versions",
                legacy_app_path=str(tmp_path / "legacy"),
                output_parent_path=str(output_dir),
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
        uow.v2_jobs.save(
            V2MigrationJobRecord(
                job_id="job-stage4",
                setup_id=setup.setup_id,
                setup_checksum="checksum",
                pipeline_id="springboot-216-to-356-java21-three-stage",
                stage_chain_json="[]",
                status="running",
                created_at=now,
                updated_at=now,
                correlation_id=None,
            )
        )
    return TestClient(create_app(lambda: SqliteUnitOfWork(conn)), base_url="http://127.0.0.1:8000"), conn


def _seed_stage_command(conn: sqlite3.Connection, *, stage: int, command_id: str, sandbox_path: Path) -> None:
    from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import V2StageCommandRecord

    now = utc_now_text()
    with SqliteUnitOfWork(conn) as uow:
        uow.v2_commands.save(
            V2StageCommandRecord(
                command_id=command_id,
                job_id="job-stage4",
                stage_index=stage,
                manifest_checksum=f"manifest-{command_id}",
                argv_json=json.dumps(["modernizer", "--sandbox", str(sandbox_path)]),
                env_json="{}",
                status="completed",
                created_at=now,
                updated_at=now,
                result_json=json.dumps({"sandbox_path": str(sandbox_path)}),
            )
        )


def _seed_stage_event(conn: sqlite3.Connection, *, stage: int, event_type: str) -> None:
    with SqliteUnitOfWork(conn) as uow:
        uow.v2_events.save(
            job_id="job-stage4",
            stage=stage,
            event_type=event_type,
            status="completed",
            message=f"Stage {stage} {event_type}.",
            payload={},
        )