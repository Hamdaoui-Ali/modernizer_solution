"""PR-G: Governed LLM invocation ledger — comprehensive tests.

Covers migration, repository CRUD, content-derived checksums,
proposer != reviewer distinct IDs, fallback tracking, security
rules (no raw secrets/endpoints/deployments), and repair chain
capture points.

Required coverage (16+ tests):
 1. migration applies on fresh DB
 2. old DB upgrades cleanly
 3. save/list/get invocation works
 4. completed invocation stores output checksum
 5. failed invocation stores redacted error
 6. list by job isolates jobs
 7. list by proposal isolates proposals
 8. proposer and reviewer invocations are distinct
 9. fallback_used is stored
10. no raw endpoint/API key/deployment secret stored or returned
11. no raw prompt/completion leaked in API response
12. context_checksum and output_checksum are content-derived
13. repair chain records proposer invocation
14. repair chain records reviewer invocation
15. revision chain records revision proposer/reviewer invocation (if path exists)
16. endpoint response has no forbidden fields
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.application.redaction import redact_model_summary
from migration_factory.control_tower.application.v2_llm_invocation_ledger import (
    V2LLMInvocationLedger,
    compute_content_checksum,
    safe_provider_alias,
)
from migration_factory.control_tower.application.v2_model_role_router import V2ModelRole
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.v2_llm_invocation_repository import (
    SqliteV2LLMInvocationRepository,
    V2LLMInvocationRecord,
)


# ── Helpers ────────────────────────────────────────────────────────────

MIGRATION_PATH = (
    "migration_factory/control_tower/infrastructure/sqlite/migrations"
    "/0050_v2_llm_invocations.sql"
)
RUNTIME_METADATA_MIGRATION_PATH = (
    "migration_factory/control_tower/infrastructure/sqlite/migrations"
    "/0060_v2_llm_invocation_runtime_metadata.sql"
)


def _apply_migration_only(tmp_path: Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    db_path = tmp_path / "test_v2_llm_invocations.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    with open(MIGRATION_PATH) as f:
        conn.executescript(f.read())
    with open(RUNTIME_METADATA_MIGRATION_PATH) as f:
        conn.executescript(f.read())
    return conn


def _make_repo(conn: sqlite3.Connection) -> SqliteV2LLMInvocationRepository:
    return SqliteV2LLMInvocationRepository(conn)


def _make_ledger(conn: sqlite3.Connection) -> V2LLMInvocationLedger:
    repo = _make_repo(conn)
    return V2LLMInvocationLedger(repo)


def _seed_invocation(
    repo: SqliteV2LLMInvocationRepository,
    *,
    job_id: str = "job-1",
    proposal_id: str | None = None,
    role: str = "main",
    responsibility: str = "repair_proposal",
    status: str = "started",
    fallback_used: int = 0,
) -> str:
    inv_id = uuid4().hex
    record = V2LLMInvocationRecord(
        invocation_id=inv_id,
        job_id=job_id,
        role=role,
        responsibility=responsibility,
        status=status,
        created_at=utc_now_text(),
        proposal_id=proposal_id,
        provider_alias=safe_provider_alias(),
        fallback_used=fallback_used,
    )
    repo.save(record)
    return inv_id


# ── 1. Migration applies on fresh DB ─────────────────────────────────


class TestMigration:
    def test_table_exists(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v2_llm_invocations'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_has_indexes(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path)
        cur = conn.cursor()
        for idx in (
            "ix_v2_llm_invocations_job_created",
            "ix_v2_llm_invocations_proposal",
            "ix_v2_llm_invocations_gate",
            "ix_v2_llm_invocations_role",
            "ix_v2_llm_invocations_status",
        ):
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
                (idx,),
            )
            assert cur.fetchone() is not None, f"missing index {idx}"
        conn.close()

    def test_append_only_triggers(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO v2_llm_invocations (invocation_id, job_id, role, responsibility, status, created_at) "
            "VALUES ('test-1', 'j1', 'main', 'repair_proposal', 'started', '2026-06-30T00:00:00.000000Z')"
        )
        with pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError), match="append-only"):
            cur.execute("DELETE FROM v2_llm_invocations WHERE invocation_id = 'test-1'")
        conn.close()

    def test_check_constraints(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path)
        cur = conn.cursor()
        with pytest.raises(sqlite3.IntegrityError):
            cur.execute(
                "INSERT INTO v2_llm_invocations (invocation_id, job_id, role, responsibility, status, created_at) "
                "VALUES ('bad-1', 'j1', 'invalid_role', 'repair_proposal', 'started', 'now')"
            )
        conn.close()


# ── 2. Old DB upgrades cleanly ────────────────────────────────────────


class TestUpgradeCompat:
    """Simulate old DB (schema before 0049) then apply the migration."""

    def test_old_db_upgrades_cleanly(self, tmp_path: Path) -> None:
        db_path = tmp_path / "upgrade_test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE migration_jobs (job_id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO migration_jobs (job_id, status) VALUES ('j1', 'running')")
        conn.commit()
        with open(MIGRATION_PATH) as f:
            conn.executescript(f.read())
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='v2_llm_invocations'"
        )
        assert cur.fetchone() is not None
        rows = cur.execute("SELECT * FROM migration_jobs").fetchall()
        assert len(rows) == 1
        conn.close()


# ── 3. Save/list/get invocation works ─────────────────────────────────


class TestRepositoryCRUD:
    def test_save_and_get(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path)
        repo = _make_repo(conn)
        inv_id = uuid4().hex
        record = V2LLMInvocationRecord(
            invocation_id=inv_id,
            job_id="job-1",
            role="main",
            responsibility="repair_proposal",
            status="started",
            created_at=utc_now_text(),
            provider_alias="azure_openai",
        )
        repo.save(record)
        loaded = repo.get(inv_id)
        assert loaded is not None
        assert loaded.invocation_id == inv_id
        assert loaded.job_id == "job-1"
        assert loaded.role == "main"
        assert loaded.responsibility == "repair_proposal"
        assert loaded.status == "started"
        conn.close()

    def test_list_by_job(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path)
        repo = _make_repo(conn)
        _seed_invocation(repo, job_id="job-a", role="main")
        _seed_invocation(repo, job_id="job-a", role="reviewer")
        _seed_invocation(repo, job_id="job-b", role="main")
        entries_a = repo.list_by_job("job-a")
        assert len(entries_a) == 2
        entries_b = repo.list_by_job("job-b")
        assert len(entries_b) == 1
        conn.close()

    def test_list_by_proposal(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path)
        repo = _make_repo(conn)
        _seed_invocation(repo, job_id="j1", proposal_id="prop-1")
        _seed_invocation(repo, job_id="j1", proposal_id="prop-1")
        _seed_invocation(repo, job_id="j1", proposal_id="prop-2")
        prop_entries = repo.list_by_proposal("prop-1")
        assert len(prop_entries) == 2
        prop2_entries = repo.list_by_proposal("prop-2")
        assert len(prop2_entries) == 1
        conn.close()


# ── 4. Completed invocation stores output checksum ────────────────────


class TestCompletion:
    def test_completed_stores_output_checksum(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path)
        ledger = _make_ledger(conn)
        inv_id = ledger.start_invocation(
            job_id="j1", role="main", responsibility="repair_proposal"
        )
        ledger.complete_invocation(
            inv_id,
            output="test output content",
            redacted_summary="Completed OK",
        )
        record = ledger.get_invocation(inv_id)
        assert record is not None
        assert record.status == "completed"
        assert record.output_checksum is not None
        assert record.output_checksum == compute_content_checksum("test output content")
        assert record.completed_at is not None
        conn.close()


# ── 5. Failed invocation stores redacted error ────────────────────────


class TestFailure:
    def test_failed_stores_redacted_error(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path)
        ledger = _make_ledger(conn)
        inv_id = ledger.start_invocation(
            job_id="j1", role="main", responsibility="repair_proposal"
        )
        ledger.fail_invocation(
            inv_id,
            redacted_error="model returned 500: timeout",
            redacted_summary="Invocation failed",
        )
        record = ledger.get_invocation(inv_id)
        assert record is not None
        assert record.status == "failed"
        assert record.redacted_error is not None
        assert record.completed_at is not None
        conn.close()


# ── 6. List by job isolates jobs ──────────────────────────────────────


class TestJobIsolation:
    def test_jobs_isolated(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path)
        ledger = _make_ledger(conn)
        ledger.start_invocation(job_id="job-x", role="main", responsibility="repair_proposal")
        ledger.start_invocation(job_id="job-y", role="reviewer", responsibility="repair_review")
        x_entries = ledger.list_by_job("job-x")
        y_entries = ledger.list_by_job("job-y")
        assert len(x_entries) == 1
        assert len(y_entries) == 1
        assert x_entries[0].job_id == "job-x"
        assert y_entries[0].job_id == "job-y"
        conn.close()


# ── 7. List by proposal isolates proposals ────────────────────────────


class TestProposalIsolation:
    def test_proposals_isolated(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path)
        ledger = _make_ledger(conn)
        p1 = ledger.start_invocation(
            job_id="j1", role="main", responsibility="repair_proposal", proposal_id="prop-a"
        )
        p2 = ledger.start_invocation(
            job_id="j1", role="reviewer", responsibility="repair_review", proposal_id="prop-a"
        )
        ledger.start_invocation(
            job_id="j1", role="main", responsibility="repair_proposal", proposal_id="prop-b"
        )
        prop_a = ledger.list_by_proposal("prop-a")
        prop_b = ledger.list_by_proposal("prop-b")
        assert len(prop_a) == 2
        assert len(prop_b) == 1
        assert p1 != p2
        conn.close()


# ── 8. Proposer and reviewer invocations are distinct ─────────────────


class TestDistinctInvocationIds:
    def test_proposer_reviewer_distinct(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path)
        ledger = _make_ledger(conn)
        prop_id = ledger.start_invocation(
            job_id="j1", role="main", responsibility="repair_proposal"
        )
        rev_id = ledger.start_invocation(
            job_id="j1", role="reviewer", responsibility="repair_review"
        )
        assert prop_id != rev_id
        conn.close()


# ── 9. Fallback_used is stored ────────────────────────────────────────


class TestFallback:
    def test_fallback_stored(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path)
        ledger = _make_ledger(conn)
        inv_id = ledger.start_invocation(
            job_id="j1", role="main", responsibility="repair_proposal"
        )
        ledger.complete_invocation(inv_id, output="fallback content", fallback_used=True)
        record = ledger.get_invocation(inv_id)
        assert record is not None
        assert record.fallback_used == 1
        assert record.status == "fallback"
        conn.close()


# ── 10. No raw endpoint/API key/deployment secret stored or returned ──


class TestNoSecretsLeaked:
    def test_no_secrets_in_dto(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path)
        ledger = _make_ledger(conn)
        inv_id = ledger.start_invocation(
            job_id="j1", role="main", responsibility="repair_proposal"
        )
        ledger.complete_invocation(inv_id, output="safe output")
        record = ledger.get_invocation(inv_id)
        assert record is not None
        dto = ledger.record_to_dto(record)
        text = json.dumps(dto).lower()
        for secret_word in ("api_key", "api-key", "apikey", "endpoint", "secret", "bearer"):
            assert secret_word not in text, f"forbidden word {secret_word} found in dto"
        assert len(dto.get("deployment_alias_hash") or "") == 0 or len(dto["deployment_alias_hash"]) <= 64
        assert dto.get("provider_alias") in (None, "azure_openai")
        forbidden = ledger.forbidden_fields_exposed(dto)
        assert len(forbidden) == 0, f"forbidden fields found: {forbidden}"
        conn.close()

    def test_no_secrets_in_api_response(self, tmp_path: Path) -> None:
        """Verify the FastAPI endpoint returns no forbidden fields."""
        conn = _apply_migration_only(tmp_path, check_same_thread=False)
        repo = _make_repo(conn)
        _seed_invocation(repo, job_id="j1")
        _seed_invocation(repo, job_id="j1", role="reviewer", responsibility="repair_review")

        app = _build_test_app_with_connection(conn)
        client = TestClient(app)
        response = client.get("/v1/v2/jobs/j1/llm/activity")
        assert response.status_code == 200
        data = response.json()
        assert "invocations" in data
        text = json.dumps(data).lower()
        for secret_word in ("api_key", "api-key", "apikey", "endpoint", "secret", "bearer", "password"):
            assert secret_word not in text, f"forbidden word {secret_word} in API response"
        conn.close()


# ── 11. No raw prompt/completion leaked in API response ───────────────


class TestNoRawContent:
    def test_no_raw_prompt_or_completion(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path, check_same_thread=False)
        repo = _make_repo(conn)
        _seed_invocation(repo, job_id="j1")

        app = _build_test_app_with_connection(conn)
        client = TestClient(app)
        response = client.get("/v1/v2/jobs/j1/llm/activity")
        assert response.status_code == 200
        data = response.json()
        invocations = data.get("invocations", [])
        for inv in invocations:
            inv_keys = set(inv.keys())
            for forbidden_key in ("prompt", "completion", "raw_content"):
                key_exact = forbidden_key
                key_alt = forbidden_key.replace("_", "")
                assert key_exact not in inv_keys, f"forbidden field {forbidden_key} in response"
                assert key_alt not in inv_keys, f"forbidden field {forbidden_key} (alt) in response"
        conn.close()


# ── 12. Checksums are content-derived ─────────────────────────────────


class TestContentDerivedChecksums:
    def test_checksums_match_content(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path)
        ledger = _make_ledger(conn)
        inv_id = ledger.start_invocation(
            job_id="j1",
            role="main",
            responsibility="repair_proposal",
            context_checksum="sha256:abc123",
            input_checksum="sha256:def456",
        )
        output_text = "exact output text for checksum verification"
        ledger.complete_invocation(inv_id, output=output_text)
        record = ledger.get_invocation(inv_id)
        assert record is not None
        expected_output_cs = compute_content_checksum(output_text)
        assert record.output_checksum == expected_output_cs
        assert record.context_checksum == "sha256:abc123"
        assert record.input_checksum == "sha256:def456"
        conn.close()


# ── 13/14. Repair chain records proposer/reviewer invocations ─────────


class TestRepairChainCapture:
    """Test that produce_repair_review_chain captures invocations via ledger."""

    def test_chain_captures_proposer_and_reviewer(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path)
        ledger = _make_ledger(conn)

        from migration_factory.control_tower.domain.checksums import sha256_canonical_json as _cs

        primary_content = json.dumps({
            "root_cause": "test failure",
            "fix_strategy": "update dependency",
            "changed_files": ["pom.xml"],
            "proposed_diff": "--- a/pom.xml\n+++ b/pom.xml\n@@ -1 +1 @@\n-test\n+fixed",
            "risk": "LOW",
            "confidence": 0.9,
            "rationale": "test",
        })

        expected_primary_checksum = _cs({
            "root_cause": "test failure",
            "fix_strategy": "update dependency",
            "changed_files": ["pom.xml"],
            "proposed_diff": "--- a/pom.xml\n+++ b/pom.xml\n@@ -1 +1 @@\n-test\n+fixed",
            "deterministic_rule_id": "",
            "risk": "LOW",
            "confidence": 0.9,
            "rationale": "test",
            "no_fix_reason": "",
        })
        expected_diff_checksum = _cs({
            "unified_diff": "--- a/pom.xml\n+++ b/pom.xml\n@@ -1 +1 @@\n-test\n+fixed",
        })

        mock_client = MagicMock()
        call_count: list[int] = [0]
        _saved_checksums: dict[str, str] = {}

        def side_effect(*, role, prompt, fallback, output_schema_name=None, require_schema=False, **kwargs):
            nonlocal _saved_checksums
            call_count[0] += 1
            if call_count[0] == 1 or role == V2ModelRole.PROPOSER:
                _saved_checksums["primary"] = expected_primary_checksum
                _saved_checksums["diff"] = expected_diff_checksum
                return MagicMock(
                    success=True,
                    content=primary_content,
                    redacted_summary="Primary repair succeeded.",
                    source="azure_openai",
                    model_status="live_ok",
                    provider="azure_openai",
                    role="proposer",
                    failure_reason="",
                )
            _saved_checksums["context"] = "cs-context"
            return MagicMock(
                success=True,
                content=json.dumps({
                    "decision": "accept",
                    "notes": ["Looks good"],
                    "risks": ["Low risk"],
                    "confidence": 0.85,
                    "policy_concerns": [],
                    "reviewed_context_checksum": _saved_checksums.get("context", "cs-context"),
                    "reviewed_primary_output_checksum": _saved_checksums.get("primary", expected_primary_checksum),
                    "reviewed_diff_checksum": _saved_checksums.get("diff", expected_diff_checksum),
                }),
                redacted_summary="Reviewer accepted.",
                source="azure_openai",
                model_status="live_ok",
                provider="azure_openai",
                role="reviewer",
                failure_reason="",
            )

        mock_client.answer_with_role.side_effect = side_effect

        from migration_factory.orchestrator.repair_review_chain import (
            produce_repair_review_chain,
        )
        from migration_factory.repair_loop.failure_evidence import (
            FailureEvidence,
            FailureSource,
            NormalizedCompilerError,
        )
        from migration_factory.repair_loop.repair_context import (
            RepairContextPack,
        )

        evidence = FailureEvidence(
            failure_source=FailureSource.BUILD,
            stage_index=2,
            failure_summary="Build failed",
            compiler_errors=[NormalizedCompilerError(message="symbol not found", file_path="Test.java")],
            test_failures=[],
            changed_files=frozenset(["pom.xml"]),
            source_profile="java11",
            target_profile="java17",
            accepted_artifact_checksums=frozenset(),
            content_checksum="cs-evidence",
        )
        from migration_factory.control_tower.domain.checksums import utc_now_text

        context_pack = RepairContextPack(
            job_id="test-job-1",
            stage_index=2,
            command_id="cmd-1",
            failure_source="build",
            failure_evidence_checksum="cs-evidence",
            context_pack_checksum="cs-context",
            base_repo_state_checksum="cs-base",
            created_at=utc_now_text(),
            source_profile="java11",
            target_profile="java17",
        )

        result = produce_repair_review_chain(
            failure_evidence=evidence,
            context_pack=context_pack,
            output_dir=tmp_path / "chain_output",
            model_client=mock_client,
            invocation_ledger=ledger,
        )

        review_chain = result.get("review_chain", {})
        prop_id = review_chain.get("proposer_invocation_id")
        rev_id = review_chain.get("reviewer_invocation_id")
        assert prop_id is not None, "proposer invocation ID missing"
        assert rev_id is not None, "reviewer invocation ID missing"
        assert prop_id != rev_id, "proposer and reviewer must be distinct"

        prop_record = ledger.get_invocation(prop_id)
        rev_record = ledger.get_invocation(rev_id)
        assert prop_record is not None
        assert rev_record is not None
        assert prop_record.role == "main"
        assert prop_record.responsibility == "repair_proposal"
        assert rev_record.role == "reviewer"
        assert rev_record.responsibility == "repair_review"
        assert prop_record.output_checksum is not None
        assert rev_record.output_checksum is not None
        conn.close()


# ── 15. Revision chain (if path exists) ───────────────────────────────


class TestRevisionChainCapture:
    def test_revision_invocations_captured(self, tmp_path: Path) -> None:
        """Test that the ledger captures revision proposer/reviewer."""
        conn = _apply_migration_only(tmp_path)
        ledger = _make_ledger(conn)

        inv_id = ledger.start_invocation(
            job_id="j1",
            role="main",
            responsibility="revision_proposal",
            proposal_id="prop-rev",
        )
        ledger.complete_invocation(inv_id, output="revised output")

        rev_inv_id = ledger.start_invocation(
            job_id="j1",
            role="reviewer",
            responsibility="revision_review",
            proposal_id="prop-rev",
        )
        ledger.complete_invocation(rev_inv_id, output="revised review")

        assert inv_id != rev_inv_id

        by_proposal = ledger.list_by_proposal("prop-rev")
        assert len(by_proposal) == 2
        roles = {r.role for r in by_proposal}
        assert "main" in roles
        assert "reviewer" in roles
        conn.close()


# ── 16. Endpoint response has no forbidden fields ─────────────────────


class TestEndpointForbiddenFields:
    def test_endpoint_no_forbidden_fields(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path, check_same_thread=False)
        repo = _make_repo(conn)
        _seed_invocation(repo, job_id="secure-job", role="main", responsibility="repair_proposal")
        _seed_invocation(repo, job_id="secure-job", role="reviewer", responsibility="repair_review")

        app = _build_test_app_with_connection(conn)
        client = TestClient(app)
        response = client.get("/v1/v2/jobs/secure-job/llm/activity")
        assert response.status_code == 200
        data = response.json()
        invocations = data.get("invocations", [])
        assert len(invocations) >= 2
        for inv in invocations:
            inv_keys = set(inv.keys())
            for forbidden in ("prompt", "completion", "endpoint", "api_key", "secret", "raw", "password"):
                assert forbidden not in inv_keys, f"found forbidden field {forbidden}"

            assert inv.get("role") in ("main", "reviewer", "fallback")
            assert inv.get("responsibility") in (
                "repair_proposal", "repair_review", "revision_proposal", "revision_review",
                "diagnosis", "explanation",
            )
        conn.close()


# ── Extra: token/latency tracking ─────────────────────────────────────


class TestTokenAndLatency:
    def test_token_and_latency_stored(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path)
        ledger = _make_ledger(conn)
        inv_id = ledger.start_invocation(
            job_id="j1", role="main", responsibility="repair_proposal"
        )
        ledger.complete_invocation(
            inv_id,
            output="test",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            latency_ms=1234,
        )
        record = ledger.get_invocation(inv_id)
        assert record is not None
        assert record.prompt_tokens == 100
        assert record.completion_tokens == 50
        assert record.total_tokens == 150
        assert record.latency_ms == 1234
        conn.close()


# ── API integration test ──────────────────────────────────────────────


def _build_test_app_with_connection(api_connection: sqlite3.Connection):
    """Build a minimal FastAPI app with the V2 LLM activity endpoint using a pre-seeded connection.

    The connection must be created with check_same_thread=False to allow TestClient
    (which runs in a separate thread) to access it.
    """
    from fastapi import FastAPI
    from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
        SqliteControlTowerUnitOfWork,
    )
    from contextlib import contextmanager

    app = FastAPI()

    @contextmanager
    def uow_factory():
        uow = SqliteControlTowerUnitOfWork(api_connection)
        yield uow

    @app.get("/v1/v2/jobs/{job_id}/llm/activity")
    def list_activity(job_id: str):
        with uow_factory() as uow:
            records = uow.v2_llm_invocations.list_by_job(job_id)
            invocations = [
                {
                    "invocation_id": r.invocation_id,
                    "job_id": r.job_id,
                    "role": r.role,
                    "responsibility": r.responsibility,
                    "status": r.status,
                    "proposal_id": r.proposal_id,
                    "gate_id": r.gate_id,
                    "provider_alias": r.provider_alias,
                    "deployment_alias_hash": r.deployment_alias_hash,
                    "context_checksum": r.context_checksum,
                    "output_checksum": r.output_checksum,
                    "schema_name": r.schema_name,
                    "fallback_used": bool(r.fallback_used),
                    "redacted_summary": r.redacted_summary,
                    "prompt_tokens": r.prompt_tokens,
                    "completion_tokens": r.completion_tokens,
                    "total_tokens": r.total_tokens,
                    "latency_ms": r.latency_ms,
                    "created_at": r.created_at,
                    "completed_at": r.completed_at,
                }
                for r in records
            ]
        return {"invocations": invocations}

    return app


class TestEndpointIntegration:
    def test_endpoint_empty(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path, check_same_thread=False)
        app = _build_test_app_with_connection(conn)
        client = TestClient(app)
        response = client.get("/v1/v2/jobs/empty-job/llm/activity")
        assert response.status_code == 200
        data = response.json()
        assert data == {"invocations": []}
        conn.close()

    def test_endpoint_with_data(self, tmp_path: Path) -> None:
        conn = _apply_migration_only(tmp_path, check_same_thread=False)
        repo = _make_repo(conn)
        _seed_invocation(repo, job_id="data-job")
        app = _build_test_app_with_connection(conn)
        client = TestClient(app)
        response = client.get("/v1/v2/jobs/data-job/llm/activity")
        assert response.status_code == 200
        data = response.json()
        assert len(data["invocations"]) == 1
        inv = data["invocations"][0]
        assert inv["role"] == "main"
        assert inv["responsibility"] == "repair_proposal"
        conn.close()
