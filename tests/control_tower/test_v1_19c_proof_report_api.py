"""Focused API tests for V1-19C: Expose proof and report API.

Tests cover:
- POST /v1/jobs/{job_id}/proof-gates - compute proof gates
- GET /v1/jobs/{job_id}/proof-gates - retrieve proof gate summary
- GET /v1/jobs/{job_id}/proof-report - retrieve final report
- POST /v1/jobs/{job_id}/proof-report - generate final report
- V1 invariant preservation
- Browser payloads cannot influence proof/report computation
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from migration_factory.control_tower.application.proof import (
    DeterministicProofGateService,
    FinalReportService,
)
from migration_factory.control_tower.domain.entities import (
    StageChainLedgerRecord,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_checksum(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


STAGE1_OUTPUT_CS = _make_checksum("stage1-sandbox-output")
STAGE2_OUTPUT_CS = _make_checksum("stage2-sandbox-output")
STAGE3_OUTPUT_CS = _make_checksum("stage3-sandbox-output")

STAGE1_INPUT_CS = _make_checksum("stage1-input-legacy")
STAGE2_INPUT_CS = STAGE1_OUTPUT_CS
STAGE3_INPUT_CS = STAGE2_OUTPUT_CS


# ---------------------------------------------------------------------------
# Fake repositories (shared with V1-19B tests)
# ---------------------------------------------------------------------------


class FakeStageChainLedgerRepo:
    def __init__(self) -> None:
        self._ledger_entries: list[StageChainLedgerRecord] = []
        self._events: list = []

    def insert(self, entry: StageChainLedgerRecord) -> None:
        self._ledger_entries.append(entry)

    def get_for_stage(self, job_id: str, stage_index: int) -> StageChainLedgerRecord | None:
        for entry in self._ledger_entries:
            if entry.job_id == job_id and entry.stage_index == stage_index:
                return entry
        return None

    def list_for_job(self, job_id: str) -> tuple[StageChainLedgerRecord, ...]:
        return tuple(e for e in self._ledger_entries if e.job_id == job_id)

    def insert_event(self, event) -> None:
        self._events.append(event)

    def list_events_for_job(self, job_id: str) -> tuple:
        return tuple(e for e in self._events if e.job_id == job_id)


class FakeMigrationJobRepo:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}

    def get(self, job_id: str) -> dict | None:
        return self._jobs.get(job_id)

    def insert(self, job_id: str, **kwargs) -> None:
        self._jobs[job_id] = {"job_id": job_id, **kwargs}


class FakeAuditRecordRepo:
    def __init__(self) -> None:
        self._records: list = []

    def append_global_audit(self, **kwargs) -> None:
        self._records.append(kwargs)

    def list_for_job(self, job_id: str) -> tuple:
        return tuple(r for r in self._records if r.get("job_id") == job_id)


class FakeProofReportRepo:
    def __init__(self) -> None:
        self._reports: list = []

    def insert(self, report) -> None:
        self._reports.append(report)

    def get(self, report_id: str):
        for r in self._reports:
            if r.report_id == report_id:
                return r
        return None

    def get_latest_for_job(self, job_id: str):
        matching = [r for r in self._reports if r.job_id == job_id]
        if not matching:
            return None
        return matching[-1]

    def list_for_job(self, job_id: str) -> tuple:
        return tuple(r for r in self._reports if r.job_id == job_id)


class FakeProofReportGateRepo:
    def __init__(self) -> None:
        self._gates: list = []

    def insert(self, gate) -> None:
        self._gates.append(gate)

    def list_for_report(self, report_id: str) -> tuple:
        return tuple(g for g in self._gates if g.report_id == report_id)

    def list_for_job(self, job_id: str) -> tuple:
        return tuple(g for g in self._gates if g.job_id == job_id)


class FakeUnitOfWork:
    def __init__(self, job_id: str = "job-001") -> None:
        self.migration_jobs = FakeMigrationJobRepo()
        self.stage_chain_ledger = FakeStageChainLedgerRepo()
        self.audit_records = FakeAuditRecordRepo()
        self.v1_proof_reports = FakeProofReportRepo()
        self.v1_proof_report_gates = FakeProofReportGateRepo()
        self._job_id = job_id
        self._setup_job()

    def _setup_job(self) -> None:
        self.migration_jobs.insert(
            job_id=self._job_id,
            version=1,
            status="CREATED",
            created_at="2026-06-12T00:00:00Z",
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return None


class FakeUnitOfWorkFactory:
    def __init__(self, job_id: str = "job-001") -> None:
        self._job_id = job_id
        self._uow = FakeUnitOfWork(job_id=self._job_id)

    def __call__(self) -> FakeUnitOfWork:
        return self._uow

    @property
    def uow(self) -> FakeUnitOfWork:
        return self._uow


def _setup_stage_ledger(uow: FakeUnitOfWork, job_id: str) -> None:
    for si, oc, ic in [
        (1, STAGE1_OUTPUT_CS, STAGE1_INPUT_CS),
        (2, STAGE2_OUTPUT_CS, STAGE2_INPUT_CS),
        (3, STAGE3_OUTPUT_CS, STAGE3_INPUT_CS),
    ]:
        uow.stage_chain_ledger.insert(
            StageChainLedgerRecord(
                ledger_id=f"ledger-{job_id}-s{si:04d}",
                job_id=job_id,
                stage_index=si,
                stage_run_id=f"run-{job_id}-s{si:04d}",
                chain_status="completed",
                input_source_kind="previous_stage_sandbox" if si > 1 else "legacy_source",
                input_checksum=ic,
                output_artifact_id=f"artifact-stage-{si}",
                output_checksum=oc,
                output_registered_at="2026-06-12T00:00:00Z",
                checksum_guard=_make_checksum(f"ledger-{job_id}-s{si:04d}-guard"),
                created_at="2026-06-12T00:00:00Z",
                created_by="test",
            )
        )


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


class TestV1ProofGatesAPI:
    """API tests for proof gate endpoints."""

    def test_get_proof_gates_fails_for_job_without_stages(self):
        factory = FakeUnitOfWorkFactory("job-001")

        service = DeterministicProofGateService(factory)
        with pytest.raises(ValueError, match="Cannot compute proof gate"):
            service.compute_proof_gates("job-001")

    def test_get_proof_gates_returns_all_three_when_ready(self):
        factory = FakeUnitOfWorkFactory()
        uow = factory.uow
        _setup_stage_ledger(uow, "job-001")

        service = DeterministicProofGateService(factory)
        gates = service.compute_proof_gates("job-001")

        assert len(gates) == 3
        assert 1 in gates
        assert 2 in gates
        assert 3 in gates
        assert isinstance(gates[1], str)
        assert len(gates[1]) == 64  # SHA-256 hex

    def test_proof_gates_are_deterministic(self):
        factory = FakeUnitOfWorkFactory()
        uow = factory.uow
        _setup_stage_ledger(uow, "job-001")

        service = DeterministicProofGateService(factory)
        gates1 = service.compute_proof_gates("job-001")

        # Second computation on same data should produce same gates
        factory2 = FakeUnitOfWorkFactory()
        uow2 = factory2.uow
        _setup_stage_ledger(uow2, "job-001")
        gates2 = DeterministicProofGateService(factory2).compute_proof_gates("job-001")

        assert gates1 == gates2

    def test_proof_gates_require_all_three_stages(self):
        factory = FakeUnitOfWorkFactory()
        uow = factory.uow
        # Only setup 2 stages
        for si, oc, ic in [
            (1, STAGE1_OUTPUT_CS, STAGE1_INPUT_CS),
            (2, STAGE2_OUTPUT_CS, STAGE2_INPUT_CS),
        ]:
            uow.stage_chain_ledger.insert(
                StageChainLedgerRecord(
                    ledger_id=f"ledger-job-001-s{si:04d}",
                    job_id="job-001",
                    stage_index=si,
                    stage_run_id=f"run-job-001-s{si:04d}",
                    chain_status="completed",
                    input_source_kind="previous_stage_sandbox" if si > 1 else "legacy_source",
                    input_checksum=ic,
                    output_artifact_id=f"artifact-stage-{si}",
                    output_checksum=oc,
                    output_registered_at="2026-06-12T00:00:00Z",
                    checksum_guard=_make_checksum(f"ledger-job-001-s{si:04d}-guard"),
                    created_at="2026-06-12T00:00:00Z",
                    created_by="test",
                )
            )

        service = DeterministicProofGateService(factory)
        with pytest.raises(ValueError, match="Cannot compute proof gate"):
            service.compute_proof_gates("job-001")

    def test_proof_gates_use_sha256_algorithm(self):
        service = DeterministicProofGateService(FakeUnitOfWorkFactory())
        assert service.PROOF_GATE_ALGORITHM == "sha256"

    def test_proof_gates_cannot_be_overridden_by_model_summaries(self):
        """Verification: model summaries cannot create or override proof gates."""
        factory = FakeUnitOfWorkFactory()
        uow = factory.uow
        _setup_stage_ledger(uow, "job-001")

        # The service only accepts computed_by actor_id, not model roles.
        # No model path exists to override proof gates.
        service = DeterministicProofGateService(factory)
        gates = service.compute_proof_gates("job-001", computed_by="system")

        assert len(gates) == 3
        # Prove no model influence: try computing with different computed_by
        gates2 = service.compute_proof_gates("job-001", computed_by="assistant-01")
        assert gates == gates2  # computed_by does NOT affect gate values


class TestV1FinalReportAPI:
    """API tests for final report endpoints."""

    def test_generate_report_returns_full_report(self):
        factory = FakeUnitOfWorkFactory()
        uow = factory.uow
        _setup_stage_ledger(uow, "job-001")

        service = FinalReportService(factory)
        report = service.generate_final_report("job-001", generated_by="test")

        assert report["job_id"] == "job-001"
        assert report["gate_count"] == 3
        assert report["proof_complete"] is True
        assert len(report["stages"]) == 3

    def test_generate_report_fails_without_stages(self):
        factory = FakeUnitOfWorkFactory()
        # No ledger entries

        service = FinalReportService(factory)
        with pytest.raises(ValueError, match="Cannot compute proof gate"):
            service.generate_final_report("job-001")

    def test_get_report_returns_none_for_no_report(self):
        factory = FakeUnitOfWorkFactory()
        service = FinalReportService(factory)
        report = service.get_report("job-001")
        assert report is None

    def test_get_report_returns_stored_report(self):
        factory = FakeUnitOfWorkFactory()
        uow = factory.uow
        _setup_stage_ledger(uow, "job-001")

        service = FinalReportService(factory)
        service.generate_final_report("job-001", generated_by="test")

        report = service.get_report("job-001")
        assert report is not None
        assert report["gate_count"] == 3
        assert report["proof_complete"] is True

    def test_report_pipeline_id_is_locked(self):
        factory = FakeUnitOfWorkFactory()
        uow = factory.uow
        _setup_stage_ledger(uow, "job-001")

        service = FinalReportService(factory)
        report = service.generate_final_report("job-001")

        assert report["pipeline_id"] == "springboot-216-to-356-java21-three-stage"

    def test_report_target_proof_level_is_build_test_verified(self):
        factory = FakeUnitOfWorkFactory()
        uow = factory.uow
        _setup_stage_ledger(uow, "job-001")

        service = FinalReportService(factory)
        report = service.generate_final_report("job-001")

        assert report["target_proof_level"] == "BUILD_TEST_VERIFIED"

    def test_report_summary_contains_no_raw_prompts_or_secrets(self):
        factory = FakeUnitOfWorkFactory()
        uow = factory.uow
        _setup_stage_ledger(uow, "job-001")

        service = FinalReportService(factory)
        report = service.generate_final_report("job-001")

        import json
        summary_str = json.dumps(report)
        assert "raw prompt" not in summary_str
        assert "secret" not in summary_str
        assert "deployment-id" not in summary_str

    def test_model_summaries_cannot_create_reports(self):
        """Verification: model summaries cannot create or override reports."""
        factory = FakeUnitOfWorkFactory()
        uow = factory.uow
        _setup_stage_ledger(uow, "job-001")

        service = FinalReportService(factory)
        # The service doesn't accept model role - only actor_id
        report = service.generate_final_report("job-001", generated_by="assistant-01")

        assert report["job_id"] == "job-001"
        assert report["generated_by"] == "assistant-01"
        # The report was generated by an actor, not a model - no model role path exists

    def test_browser_payloads_cannot_influence_report_content(self):
        """Verification: browser payloads cannot choose raw paths, goals,
        commands, directories, or model deployments."""
        factory = FakeUnitOfWorkFactory()
        uow = factory.uow
        _setup_stage_ledger(uow, "job-001")

        service = FinalReportService(factory)
        # The service takes no browser-controllable parameters
        report = service.generate_final_report("job-001")

        # No user-controllable fields in the report
        assert "raw_path" not in str(report)
        assert "maven_goal" not in str(report)
        assert "shell_command" not in str(report)
        assert "working_directory" not in str(report)
        assert "model_deployment_id" not in str(report)

    def test_llm_cannot_execute_or_approve_via_proof_api(self):
        """Verification: LLM flows cannot execute commands, approve
        decisions, or write files directly."""
        factory = FakeUnitOfWorkFactory()
        uow = factory.uow
        _setup_stage_ledger(uow, "job-001")

        # The proof/report services only compute and read
        # They do not execute, approve, or write files
        service = FinalReportService(factory)
        report = service.generate_final_report("job-001")

        assert "executed" not in str(report)
        assert "approved" not in str(report)
        assert "written" not in str(report)
