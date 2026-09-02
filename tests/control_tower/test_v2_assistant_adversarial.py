"""Adversarial assistant authority tests — confirm assistant cannot execute/approve/write."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork


def _mutation_headers():
    from migration_factory.control_tower.adapters.fastapi.security import DEFAULT_FRONTEND_CLIENT_ID
    return {
        "Content-Type": "application/json",
        "Origin": "http://127.0.0.1:3000",
        "X-Control-Tower-Client": DEFAULT_FRONTEND_CLIENT_ID,
    }


def _api_client(tmp_path: Path):
    from migration_factory.control_tower.adapters.fastapi import create_app
    conn = sqlite3.connect(
        tmp_path / "adversarial_test.sqlite3",
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


class TestAssistantCannotExecute:
    """The assistant service must not allow execution, approval, or file writes."""

    def test_assistant_message_does_not_execute(self, tmp_path: Path) -> None:
        """POST assistant message must not execute any commands."""
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/jobs/j-1/assistant/messages",
            json={
                "job_id": "j-1",
                "role": "user",
                "content": "execute ls -la",
            },
            headers=_mutation_headers(),
        )
        # Should succeed — assistant messages are just stored, not executed
        assert response.status_code == 200
        body = response.json()
        assert body["role"] == "user"
        assert body["content"] == "execute ls -la"

    def test_no_execute_endpoint_exists(self, tmp_path: Path) -> None:
        """There must be no API endpoint that allows the assistant to execute commands."""
        client, conn = _api_client(tmp_path)
        # Try common execution patterns
        for path in [
            "/v1/v2/jobs/j-1/assistant/execute",
            "/v1/v2/jobs/j-1/assistant/run",
            "/v1/v2/jobs/j-1/assistant/shell",
            "/v1/v2/jobs/j-1/assistant/command",
        ]:
            response = client.post(path, json={}, headers=_mutation_headers())
            assert response.status_code == 404, f"Path {path} should not exist"

    def test_no_approve_endpoint_in_assistant(self, tmp_path: Path) -> None:
        """The assistant must not have approve/reject endpoints."""
        client, conn = _api_client(tmp_path)
        for path in [
            "/v1/v2/jobs/j-1/assistant/approve",
            "/v1/v2/jobs/j-1/assistant/reject",
            "/v1/v2/jobs/j-1/assistant/decide",
        ]:
            response = client.post(path, json={}, headers=_mutation_headers())
            assert response.status_code == 404, f"Path {path} should not exist"

    def test_no_write_endpoint_in_assistant(self, tmp_path: Path) -> None:
        """The assistant must not have file write endpoints."""
        client, conn = _api_client(tmp_path)
        for path in [
            "/v1/v2/jobs/j-1/assistant/write",
            "/v1/v2/jobs/j-1/assistant/write-file",
            "/v1/v2/jobs/j-1/assistant/patch",
        ]:
            response = client.post(path, json={}, headers=_mutation_headers())
            assert response.status_code == 404, f"Path {path} should not exist"

    def test_assistant_cannot_change_route(self, tmp_path: Path) -> None:
        """The assistant must not have route/stage change endpoints."""
        client, conn = _api_client(tmp_path)
        for path in [
            "/v1/v2/jobs/j-1/assistant/change-route",
            "/v1/v2/jobs/j-1/assistant/change-stage",
            "/v1/v2/jobs/j-1/assistant/override-proof",
        ]:
            response = client.post(path, json={}, headers=_mutation_headers())
            assert response.status_code == 404, f"Path {path} should not exist"

    def test_draft_action_does_not_execute(self, tmp_path: Path) -> None:
        """Drafting an action must not execute it."""
        client, conn = _api_client(tmp_path)
        response = client.post(
            "/v1/v2/jobs/j-1/assistant/actions/draft",
            json={
                "job_id": "j-1",
                "action_type": "diagnose_failure",
                "reason": "Test reason",
                "stage_index": 1,
            },
            headers=_mutation_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        # Draft must have status "draft", not "executed" or "approved"
        assert body["status"] == "draft", "Draft must not be executed"

    def test_forbidden_capabilities_defined(self, tmp_path: Path) -> None:
        """The FORBIDDEN_CAPABILITIES list must be present in the service module."""
        from migration_factory.control_tower.application.v2_assistant_service import (
            FORBIDDEN_CAPABILITIES,
        )
        forbidden = set(FORBIDDEN_CAPABILITIES)
        assert "execute_command" in forbidden
        assert "approve_decision" in forbidden
        assert "write_file" in forbidden
        assert "change_route" in forbidden
        assert "change_stage" in forbidden
        assert "override_proof" in forbidden

    def test_allowed_tools_are_read_only(self, tmp_path: Path) -> None:
        """Allowed tools must be read-only or draft actions only."""
        from migration_factory.control_tower.application.v2_assistant_service import (
            ALLOWED_TOOLS,
        )
        allowed = set(ALLOWED_TOOLS)
        assert "explain_status" in allowed
        assert "summarize_evidence" in allowed
        assert "diagnose_failure" in allowed
        assert "draft_plan_instruction" in allowed
        assert "draft_repair_instruction" in allowed
        assert "request_action" in allowed
        # None of these should be execute/approve/write
        assert "execute_command" not in allowed
        assert "approve_decision" not in allowed
        assert "write_file" not in allowed
