"""Focused tests for V1-09: Register Azure model profiles."""

from __future__ import annotations

import json
import sqlite3

import pytest

from migration_factory.control_tower.domain.model_profiles import V1ModelProfileRecord


# ── Migration tests ──────────────────────────────────────────────────


class TestV1ModelProfilesMigration:
    """v1_model_profiles SQL migration produces correct schema and seed data."""

    MIGRATION_PATH = (
        "migration_factory/control_tower/infrastructure/sqlite/migrations"
        "/0011_v1_model_profiles.sql"
    )

    def _apply_migration(self, tmp_path) -> sqlite3.Connection:
        db_path = tmp_path / "test_v1_model_profiles.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        with open(self.MIGRATION_PATH) as f:
            cur.executescript(f.read())
        return conn

    def test_profiles_table_exists(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v1_model_profiles'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_events_table_exists(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v1_model_profile_events'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_default_fake_profile_is_seeded(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT profile_id, provider_kind, model_env_ref, endpoint_env_ref, deployment_env_ref "
            "FROM v1_model_profiles WHERE profile_id = 'default-fake'"
        )
        row = cur.fetchone()
        assert row is not None
        assert row["provider_kind"] == "fake"
        assert row["model_env_ref"] == "V1_MODEL_NAME"
        assert row["endpoint_env_ref"] == "V1_MODEL_ENDPOINT"
        assert row["deployment_env_ref"] == "V1_MODEL_DEPLOYMENT"
        conn.close()

    def test_profiles_accepts_new_entries(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO v1_model_profiles "
            "(profile_id, display_name, provider_kind, model_env_ref, "
            "endpoint_env_ref, deployment_env_ref, is_active, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, '2026-06-12T00:00:00.000000Z', 'test')",
            ("test-azure", "Test Azure profile", "azure_openai",
             "AZURE_OPENAI_MODEL", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"),
        )
        cur.execute("SELECT profile_id FROM v1_model_profiles WHERE profile_id = 'test-azure'")
        assert cur.fetchone() is not None
        conn.close()

    def test_profiles_is_append_only(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError), match="append-only"):
            cur.execute(
                "UPDATE v1_model_profiles SET display_name = 'X' WHERE profile_id = 'default-fake'"
            )
        with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError), match="append-only"):
            cur.execute("DELETE FROM v1_model_profiles WHERE profile_id = 'default-fake'")
        conn.close()

    def test_events_is_append_only(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO v1_model_profile_events "
            "(event_id, profile_id, event_type, provider_kind, "
            "actor_type, actor_id, payload_json, payload_checksum, created_at) "
            "VALUES ('evt1', 'default-fake', 'runner_validation', 'fake', "
            "'sys', 'test', '{}', 'abc', '2026-06-12T00:00:00.000000Z')"
        )
        with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError), match="append-only"):
            cur.execute("UPDATE v1_model_profile_events SET provider_kind = 'azure_openai' WHERE event_id = 'evt1'")
        with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError), match="append-only"):
            cur.execute("DELETE FROM v1_model_profile_events WHERE event_id = 'evt1'")
        conn.close()

    def test_provider_kind_check_constraint(self, tmp_path) -> None:
        conn = self._apply_migration(tmp_path)
        cur = conn.cursor()
        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                "INSERT INTO v1_model_profiles "
                "(profile_id, display_name, provider_kind, model_env_ref, "
                "endpoint_env_ref, deployment_env_ref, is_active, created_at, created_by) "
                "VALUES ('bad', 'Bad', 'invalid_provider', 'M', 'E', 'D', 1, '2026-06-12T00:00:00.000000Z', 'test')"
            )
        conn.close()


# ── Domain model tests ────────────────────────────────────────────────


class TestV1ModelProfileRecord:
    """V1ModelProfileRecord dataclass correctly models the domain."""

    def test_constructs_minimal(self) -> None:
        record = V1ModelProfileRecord(
            profile_id="test-1",
            display_name="Test",
            provider_kind="fake",
            model_env_ref="V1_MODEL_NAME",
            endpoint_env_ref="V1_MODEL_ENDPOINT",
            deployment_env_ref="V1_MODEL_DEPLOYMENT",
            is_active=True,
            created_at="2026-06-12T00:00:00.000000Z",
            created_by="test-user",
        )
        assert record.profile_id == "test-1"
        assert record.provider_kind == "fake"
        assert record.is_active is True

    def test_frozen_and_slots(self) -> None:
        record = V1ModelProfileRecord(
            profile_id="test-2",
            display_name="Azure test",
            provider_kind="azure_openai",
            model_env_ref="AZURE_MODEL",
            endpoint_env_ref="AZURE_ENDPOINT",
            deployment_env_ref="AZURE_DEPLOYMENT",
            is_active=True,
            created_at="2026-06-12T00:00:00.000000Z",
            created_by="admin",
        )
        with pytest.raises(AttributeError):
            record.profile_id = "changed"  # type: ignore[misc]

    def test_env_refs_are_refs_not_values(self) -> None:
        """Model config is stored as env refs only (acceptance criteria)."""
        record = V1ModelProfileRecord(
            profile_id="env-ref-test",
            display_name="Env ref test",
            provider_kind="fake",
            model_env_ref="V1_MODEL_NAME",
            endpoint_env_ref="V1_MODEL_ENDPOINT",
            deployment_env_ref="V1_MODEL_DEPLOYMENT",
            is_active=True,
            created_at="2026-06-12T00:00:00.000000Z",
            created_by="test",
        )
        # Verify these are env var names, not raw values.
        assert record.model_env_ref == "V1_MODEL_NAME"
        assert record.endpoint_env_ref == "V1_MODEL_ENDPOINT"
        assert record.deployment_env_ref == "V1_MODEL_DEPLOYMENT"
        # No raw secrets, prompts, or deployment IDs stored directly.
        assert not record.model_env_ref.startswith("gpt-")
        assert not record.model_env_ref.startswith("https://")

    def test_event_record_rejects_unsupported_event_type(self) -> None:
        """V1ModelProfileEventRecord raises ValueError for unsupported event types."""
        from migration_factory.control_tower.domain.model_profiles import (
            V1ModelProfileEventRecord,
        )
        with pytest.raises(ValueError, match="Unsupported model profile event type"):
            V1ModelProfileEventRecord(
                event_id="bad-evt",
                profile_id="test-p",
                event_type="chain_created",
                provider_kind="fake",
                actor_type="sys",
                actor_id="test",
                payload_json="{}",
                payload_checksum="abc",
                created_at="2026-06-12T00:00:00.000000Z",
            )

    def test_event_record_accepts_runner_validation_event_type(self) -> None:
        """V1ModelProfileEventRecord accepts runner_validation event type."""
        from migration_factory.control_tower.domain.model_profiles import (
            V1ModelProfileEventRecord,
        )
        record = V1ModelProfileEventRecord(
            event_id="valid-evt",
            profile_id="test-p",
            event_type="runner_validation",
            provider_kind="fake",
            actor_type="api",
            actor_id="tester",
            payload_json="{}",
            payload_checksum="def",
            created_at="2026-06-12T00:00:00.000000Z",
        )
        assert record.event_type == "runner_validation"


# ── Repository tests ──────────────────────────────────────────────────


class TestSqliteV1ModelProfileRepository:
    """SQLite repository for v1_model_profiles works correctly."""

    def _make_conn(self, tmp_path) -> sqlite3.Connection:
        from migration_factory.control_tower.infrastructure.sqlite.v1_model_profile_repository import (
            SqliteV1ModelProfileRepository,
        )
        db_path = tmp_path / "test_repo.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        # Apply migration.
        with open(TestV1ModelProfilesMigration.MIGRATION_PATH) as f:
            conn.cursor().executescript(f.read())
        return conn

    def test_insert_and_get(self, tmp_path) -> None:
        from migration_factory.control_tower.infrastructure.sqlite.v1_model_profile_repository import (
            SqliteV1ModelProfileRepository,
        )
        conn = self._make_conn(tmp_path)
        repo = SqliteV1ModelProfileRepository(conn)

        record = V1ModelProfileRecord(
            profile_id="my-profile",
            display_name="My Profile",
            provider_kind="fake",
            model_env_ref="M",
            endpoint_env_ref="E",
            deployment_env_ref="D",
            is_active=True,
            created_at="2026-06-12T00:00:00.000000Z",
            created_by="tester",
        )
        repo.insert(record)

        fetched = repo.get("my-profile")
        assert fetched is not None
        assert fetched.profile_id == "my-profile"
        assert fetched.display_name == "My Profile"
        assert fetched.provider_kind == "fake"
        conn.close()

    def test_get_nonexistent(self, tmp_path) -> None:
        from migration_factory.control_tower.infrastructure.sqlite.v1_model_profile_repository import (
            SqliteV1ModelProfileRepository,
        )
        conn = self._make_conn(tmp_path)
        repo = SqliteV1ModelProfileRepository(conn)
        assert repo.get("nonexistent") is None
        conn.close()

    def test_list_includes_seed_and_inserted(self, tmp_path) -> None:
        from migration_factory.control_tower.infrastructure.sqlite.v1_model_profile_repository import (
            SqliteV1ModelProfileRepository,
        )
        conn = self._make_conn(tmp_path)
        repo = SqliteV1ModelProfileRepository(conn)

        # Should have the seed profile.
        all_profiles = repo.list()
        assert any(p.profile_id == "default-fake" for p in all_profiles)

        # Insert another.
        repo.insert(
            V1ModelProfileRecord(
                profile_id="second",
                display_name="Second",
                provider_kind="azure_openai",
                model_env_ref="M2",
                endpoint_env_ref="E2",
                deployment_env_ref="D2",
                is_active=True,
                created_at="2026-06-12T00:00:00.000000Z",
                created_by="tester",
            )
        )
        all_profiles = repo.list()
        assert len(all_profiles) == 2
        assert any(p.profile_id == "second" for p in all_profiles)
        conn.close()


# ── API integration tests ─────────────────────────────────────────────


_MUTATION_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "http://127.0.0.1:3000",
    "X-Control-Tower-Client": "control-tower-frontend",
}


class TestV1ModelProfilesApi:
    """Model profile API endpoints work correctly."""

    def test_list_model_profiles_returns_seeded(self, tmp_path, control_tower_app) -> None:
        """GET /v1/model-profiles includes the default-fake seed profile."""
        client = control_tower_app
        response = client.get("/v1/model-profiles")
        assert response.status_code == 200
        data = response.json()
        assert "model_profiles" in data
        profiles = data["model_profiles"]
        assert any(p["profile_id"] == "default-fake" for p in profiles)

    def test_register_and_get_model_profile(self, tmp_path, control_tower_app) -> None:
        """POST then GET a model profile."""
        client = control_tower_app
        profile_id = "test-azure-001"
        payload = {
            "profile_id": profile_id,
            "display_name": "Test Azure OpenAI",
            "actor_id": "test-user",
        }
        resp = client.post("/v1/model-profiles", content=json.dumps(payload), headers=_MUTATION_HEADERS)
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert created["profile_id"] == profile_id
        assert created["display_name"] == "Test Azure OpenAI"
        assert created["status"] == "active"
        assert "provider_kind" not in created
        assert created["is_active"] is True

        # Retrieve by ID.
        get_resp = client.get(f"/v1/model-profiles/{profile_id}")
        assert get_resp.status_code == 200
        fetched = get_resp.json()
        assert fetched["profile_id"] == profile_id
        serialized = json.dumps(fetched)
        for forbidden in (
            "provider_kind",
            "model_env_ref",
            "endpoint_env_ref",
            "deployment_env_ref",
        ):
            assert forbidden not in serialized

    def test_register_model_profile_rejects_runtime_fields(self, tmp_path, control_tower_app) -> None:
        client = control_tower_app
        payload = {
            "profile_id": "runtime-fields",
            "display_name": "Runtime fields",
            "provider_kind": "azure_openai",
            "model_env_ref": "AZURE_MODEL_NAME",
            "endpoint_env_ref": "AZURE_ENDPOINT_URL",
            "deployment_env_ref": "AZURE_DEPLOYMENT_ID",
        }
        resp = client.post("/v1/model-profiles", content=json.dumps(payload), headers=_MUTATION_HEADERS)
        assert resp.status_code == 422

    def test_register_model_profile_missing_fields(self, tmp_path, control_tower_app) -> None:
        """POST without required fields returns 400."""
        client = control_tower_app
        resp = client.post(
            "/v1/model-profiles",
            content=json.dumps({"provider_kind": "fake"}),
            headers=_MUTATION_HEADERS,
        )
        assert resp.status_code == 422

    def test_register_model_profile_invalid_provider_defaults_to_fake(
        self, tmp_path, control_tower_app
    ) -> None:
        """Invalid provider_kind is coerced to 'fake'."""
        client = control_tower_app
        resp = client.post(
            "/v1/model-profiles",
            content=json.dumps({
                "profile_id": "test-invalid-provider",
                "display_name": "Invalid provider test",
            }),
            headers=_MUTATION_HEADERS,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["profile_id"] == "test-invalid-provider"
        assert "provider_kind" not in data

    def test_get_nonexistent_model_profile_returns_404(
        self, tmp_path, control_tower_app
    ) -> None:
        """GET for a non-existent profile returns 404."""
        client = control_tower_app
        resp = client.get("/v1/model-profiles/does-not-exist")
        assert resp.status_code == 404


# ── Fixtures for API tests ───────────────────────────────────────────


@pytest.fixture
def control_tower_app(tmp_path):
    """Create a test FastAPI app with the V1 model profiles migration applied."""
    from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
    from migration_factory.control_tower.adapters.fastapi.app import create_app
    from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork

    db_path = tmp_path / "test_control_tower.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    apply_pending_migrations(conn)

    from fastapi.testclient import TestClient

    return TestClient(
        create_app(lambda: SqliteUnitOfWork(conn)),
        base_url="http://127.0.0.1:8000",
    )
