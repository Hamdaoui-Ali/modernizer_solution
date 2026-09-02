from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.context_packs import ContextPackManifestService
from migration_factory.control_tower.application.plan_amendments import PlanAmendmentService, PlanChange
from migration_factory.control_tower.application.plan_proposals import FakeProviderPlanProposalService
from migration_factory.control_tower.application.services import ModelInvocationAuditService
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower.transition_helpers import seed_job


def _connection(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "v1_12b_validator.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    seed_job(conn)
    conn.commit()
    return conn


def _amendment_id(conn: sqlite3.Connection) -> str:
    service = PlanAmendmentService(lambda: SqliteUnitOfWork(conn))
    amendment = service.create_amendment(
        job_id="job-1",
        source_kind="manual",
        title="Operator request",
        summary="Please improve diagnostic notes only",
        notes=("safe",),
        changes=(PlanChange(stage_index=1, change_type="documentation", description="Clarify stage notes"),),
        created_by="tester",
    )
    return amendment.amendment_id


def _refs(conn: sqlite3.Connection) -> tuple[str, str]:
    invocations = ModelInvocationAuditService(lambda: SqliteUnitOfWork(conn))
    invocations.record_invocation(
        invocation_id="inv-1",
        job_id="job-1",
        profile_id="default-fake",
        provider_kind="fake",
        model_name="fake-provider",
        total_tokens=42,
        redacted_summary="Safe fake provider invocation",
        actor_type="system",
        actor_id="tester",
    )
    manifests = ContextPackManifestService(lambda: SqliteUnitOfWork(conn))
    manifests.persist_manifest(
        manifest_id="cp-1",
        pack_type="plan",
        pack_version="1.0",
        title="Plan context",
        job_id="job-1",
        created_by="tester",
    )
    return "inv-1", "cp-1"


def _valid_output(**overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "Fake-provider plan proposal",
        "summary": "Documentation-only revision proposal",
        "notes": ["derived from redacted context"],
        "changes": [
            {
                "stage_index": 1,
                "change_type": "documentation",
                "description": "Clarify stage checklist",
                "rationale": "Safer operator handoff",
            }
        ],
        "confidence_label": "medium",
        "confidence_score": 0.72,
    }
    payload.update(overrides)
    return payload


def test_valid_output_produces_pass_and_persists_revision(tmp_path: Path) -> None:
    conn = _connection(tmp_path)
    amendment_id = _amendment_id(conn)
    model_invocation_id, context_pack_manifest_id = _refs(conn)
    service = FakeProviderPlanProposalService(lambda: SqliteUnitOfWork(conn))

    report = service.create_revision_from_fake_provider(
        amendment_id=amendment_id,
        raw_output=_valid_output(),
        created_by="tester",
        model_invocation_id=model_invocation_id,
        context_pack_manifest_id=context_pack_manifest_id,
    )

    assert report.validation_status == "PASS"
    assert report.revision_persisted is True
    assert report.revision_id is not None
    assert report.revision_order == 1
    assert conn.execute("SELECT COUNT(*) FROM v1_plan_revisions").fetchone()[0] == 1


def test_pass_fields_are_deterministic(tmp_path: Path) -> None:
    conn = _connection(tmp_path)
    service = FakeProviderPlanProposalService(lambda: SqliteUnitOfWork(conn))

    first = service.validate_output(_valid_output(), model_invocation_id="inv-1", context_pack_manifest_id="cp-1")
    second = service.validate_output(_valid_output(), model_invocation_id="inv-1", context_pack_manifest_id="cp-1")

    assert first.validation_status == second.validation_status == "PASS"
    assert first.payload_checksum == second.payload_checksum
    assert first.warning_codes == second.warning_codes
    assert first.rejection_codes == second.rejection_codes
    assert first.redacted_summary == second.redacted_summary


def test_malformed_output_produces_failed(tmp_path: Path) -> None:
    conn = _connection(tmp_path)
    service = FakeProviderPlanProposalService(lambda: SqliteUnitOfWork(conn))

    report = service.validate_output("not-a-structured-payload")

    assert report.validation_status == "FAILED"
    assert report.revision_persisted is False
    assert report.rejection_codes == ("MALFORMED_PAYLOAD",)


def test_unsafe_output_produces_failed(tmp_path: Path) -> None:
    conn = _connection(tmp_path)
    service = FakeProviderPlanProposalService(lambda: SqliteUnitOfWork(conn))

    report = service.validate_output(
        _valid_output(summary=r"Use C:\Users\ilyas\secret.txt and DEPLOYMENT_ID=prod-secret")
    )

    assert report.validation_status == "FAILED"
    assert "UNSAFE_ABSOLUTE_PATH_CONTENT" in report.rejection_codes
    assert "UNSAFE_ENV_CONTENT" in report.rejection_codes


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "route",
        "ledger_id",
        "command_id",
        "arguments",
        "approval_id",
        "approval_checksum",
        "artifact_id",
        "artifact_path",
        "run_configuration",
        "target_proof_level",
        "achieved_proof_level",
        "source_path",
        "sandbox_path",
    ],
)
def test_authority_changing_output_is_rejected(tmp_path: Path, forbidden_field: str) -> None:
    conn = _connection(tmp_path)
    service = FakeProviderPlanProposalService(lambda: SqliteUnitOfWork(conn))

    report = service.validate_output(_valid_output(**{forbidden_field: "forbidden"}))

    assert report.validation_status == "FAILED"
    assert any(code.startswith("FORBIDDEN_FIELD:") for code in report.rejection_codes)


def test_invalid_advisory_is_recorded_but_does_not_block_or_mutate_job(tmp_path: Path) -> None:
    conn = _connection(tmp_path)
    amendment_id = _amendment_id(conn)
    service = FakeProviderPlanProposalService(lambda: SqliteUnitOfWork(conn))
    before_job = conn.execute(
        "SELECT version, status, last_event_sequence FROM migration_jobs WHERE job_id = 'job-1'"
    ).fetchone()
    before_revisions = conn.execute("SELECT COUNT(*) FROM v1_plan_revisions").fetchone()[0]
    before_audit = conn.execute("SELECT COUNT(*) FROM audit_records").fetchone()[0]

    report = service.create_revision_from_fake_provider(
        amendment_id=amendment_id,
        raw_output=_valid_output(route="springboot-4"),
        created_by="tester",
    )

    assert report.validation_status == "FAILED"
    assert report.revision_persisted is False
    assert conn.execute("SELECT COUNT(*) FROM v1_plan_revisions").fetchone()[0] == before_revisions
    assert conn.execute("SELECT COUNT(*) FROM audit_records").fetchone()[0] == before_audit + 1
    after_job = conn.execute(
        "SELECT version, status, last_event_sequence FROM migration_jobs WHERE job_id = 'job-1'"
    ).fetchone()
    assert tuple(before_job) == tuple(after_job)


def test_fake_provider_proposal_does_not_apply_amendment_or_execution_state(tmp_path: Path) -> None:
    conn = _connection(tmp_path)
    amendment_id = _amendment_id(conn)
    model_invocation_id, context_pack_manifest_id = _refs(conn)
    service = FakeProviderPlanProposalService(lambda: SqliteUnitOfWork(conn))
    before_amendments = conn.execute("SELECT COUNT(*) FROM v1_plan_amendments").fetchone()[0]

    report = service.create_revision_from_fake_provider(
        amendment_id=amendment_id,
        raw_output=_valid_output(),
        created_by="tester",
        model_invocation_id=model_invocation_id,
        context_pack_manifest_id=context_pack_manifest_id,
    )

    assert report.validation_status == "PASS"
    assert conn.execute("SELECT COUNT(*) FROM v1_plan_amendments").fetchone()[0] == before_amendments
    job = conn.execute("SELECT status, version FROM migration_jobs WHERE job_id = 'job-1'").fetchone()
    assert tuple(job) == ("CREATED", 1)
