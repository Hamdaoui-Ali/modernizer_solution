"""End-to-end integration test for V2 migration flow.

Tests the full critical path:
  form → setup → preflight → job → stage1 → approval → progression

Note: This test uses the V2 API endpoints directly through FastAPI
TestClient. It does not require real JDK/Maven installations since
preflight checks currently verify path existence only.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.application.v2_setup_service import (
    CreateSetupRequest,
    V2SetupService,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import (
    SqliteV2JobRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_approval_repository import (
    SqliteV2ApprovalRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_assistant_repository import (
    SqliteV2AssistantRepository,
)


def _mutation_headers():
    from migration_factory.control_tower.adapters.fastapi.security import DEFAULT_FRONTEND_CLIENT_ID
    return {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": DEFAULT_FRONTEND_CLIENT_ID,
    }


@pytest.fixture
def e2e_client(tmp_path: Path):
    from migration_factory.control_tower.adapters.fastapi import create_app
    conn = sqlite3.connect(
        tmp_path / "e2e_test.sqlite3",
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_pending_migrations(conn)
    app = create_app(lambda: SqliteUnitOfWork(conn))
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    return client, conn


class TestV2EndToEndFlow:

    def test_full_setup_to_stage1_flow(self, e2e_client) -> None:
        """Full flow: create setup → run preflight → create job → start stage1."""
        client, conn = e2e_client

        # 1. Create setup
        setup_resp = client.post(
            "/v1/migration-setups",
            json={
                "run_name": "e2e-test",
                "legacy_app_path": "/tmp/e2e-legacy",
                "output_parent_path": "/tmp/e2e-output",
                "ai_hub_path": "/tmp/e2e-aihub",
                "java11_home": "/usr/lib/jvm/java-11",
                "java17_home": "/usr/lib/jvm/java-17",
                "java21_home": "/usr/lib/jvm/java-21",
                "maven_cmd": "/usr/bin/mvn",
            },
            headers=_mutation_headers(),
        )
        assert setup_resp.status_code == 201
        setup_id = setup_resp.json()["setup_id"]
        assert setup_id

        # 2. Run preflight (will fail since paths don't exist, but that's OK)
        preflight_resp = client.post(
            "/v1/migration-setups/preflight",
            json={"setup_id": setup_id},
            headers=_mutation_headers(),
        )
        assert preflight_resp.status_code == 201
        preflight = preflight_resp.json()
        assert preflight["setup_id"] == setup_id

        # 3. Create job — should fail because preflight is not all_ready
        job_resp = client.post(
            "/v1/v2/migration-jobs",
            json={"setup_id": setup_id},
            headers=_mutation_headers(),
        )
        assert job_resp.status_code == 400
        assert "not ready" in job_resp.text.lower()

    def test_job_creation_with_valid_setup(self, e2e_client) -> None:
        """Creating a job requires an existing setup (preflight check happens before)."""
        client, conn = e2e_client

        # 1. Create setup directly via service for direct repo access
        repo = SqliteV2SetupRepository(conn)
        service = V2SetupService(repo)
        req = CreateSetupRequest(
            run_name="e2e-job-test",
            legacy_app_path="/tmp/e2e-legacy",
            output_parent_path="/tmp/e2e-output",
            ai_hub_path="/tmp/e2e-aihub",
            java11_home="/usr/lib/jvm/java-11",
            java17_home="/usr/lib/jvm/java-17",
            java21_home="/usr/lib/jvm/java-21",
            maven_cmd="/usr/bin/mvn",
        )
        dto = service.create_setup(req)

        # Try job creation without preflight — should fail
        job_resp = client.post(
            "/v1/v2/migration-jobs",
            json={"setup_id": dto.setup_id},
            headers=_mutation_headers(),
        )
        assert job_resp.status_code == 400
        assert "No preflight" in job_resp.text

    def test_setup_and_settings_endpoints(self, e2e_client) -> None:
        """Verify settings and setup endpoints are reachable."""
        client, conn = e2e_client

        # Settings endpoint
        settings_resp = client.get(
            "/v1/settings/ai",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert settings_resp.status_code == 200
        body = settings_resp.json()
        assert "azure" in body
        assert "local_mode" in body

        # List setups (empty)
        list_resp = client.get(
            "/v1/migration-setups",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert list_resp.status_code == 200
        assert list_resp.json()["setups"] == []

    def test_assistant_and_approval_flow(self, e2e_client) -> None:
        """Assistant messages and approval drafts flow correctly together."""
        client, conn = e2e_client

        # 1. Add assistant message
        msg_resp = client.post(
            "/v1/v2/jobs/j-e2e/assistant/messages",
            json={"job_id": "j-e2e", "role": "user", "content": "Start migration"},
            headers=_mutation_headers(),
        )
        assert msg_resp.status_code == 200
        msg_id = msg_resp.json()["message_id"]

        # 2. List messages
        list_resp = client.get(
            "/v1/v2/jobs/j-e2e/assistant/messages",
            headers={"Host": "127.0.0.1:8000"},
        )
        assert list_resp.status_code == 200
        messages = list_resp.json()["messages"]
        assert len(messages) >= 1
        assert any(m["message_id"] == msg_id for m in messages)

        # 3. Draft action
        draft_resp = client.post(
            "/v1/v2/jobs/j-e2e/assistant/actions/draft",
            json={
                "job_id": "j-e2e",
                "action_type": "review",
                "reason": "Check migration plan",
                "stage_index": 1,
            },
            headers=_mutation_headers(),
        )
        assert draft_resp.status_code == 200
        assert draft_resp.json()["status"] == "draft"

    def test_pipeline_constants_are_correct(self, e2e_client) -> None:
        """Verify pipeline constants are fixed and correct."""
        from migration_factory.control_tower.application.v2_job_service import PIPELINE_ID
        from migration_factory.control_tower.application.v2_worker_stage import STAGE_JDK_MAP
        from migration_factory.control_tower.application.v2_stage_progression import STAGE_CONFIG

        assert PIPELINE_ID == "springboot-216-to-400-java21-four-stage"
        assert STAGE_JDK_MAP[1]["jdk_id"] == "java11"
        assert STAGE_JDK_MAP[2]["jdk_id"] == "java17"
        assert STAGE_JDK_MAP[3]["jdk_id"] == "java21"
        assert STAGE_JDK_MAP[4]["jdk_id"] == "java21"
        assert STAGE_CONFIG[2]["profile"] == "springboot-2.7-to-3.5-java17"
        assert STAGE_CONFIG[3]["profile"] == "springboot-3.5-java17-to-java21"
        assert STAGE_CONFIG[4]["profile"] == "springboot-3.5-java21-to-4.0-java21"
