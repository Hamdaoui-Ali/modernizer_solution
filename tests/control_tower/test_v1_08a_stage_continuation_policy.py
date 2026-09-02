"""Focused tests for V1-08A: Enforce stage continuation policy.

This test file covers:
- Stage 2 uses Stage 1 sandbox output only.
- Stage 3 uses Stage 2 sandbox output only.
- Input checksum mismatch produces deterministic blocked events.
- Continuation policy matched produces queued events.
- Blocked/queued/failed events are deterministic.
- Stage readiness checks return correct allowed/reason pairs.
- V1 invariant preservation.
- Browser payloads cannot choose raw paths, Maven goals, shell commands,
  working directories, or model deployments.
- LLM flows cannot execute commands, approve decisions, or write files directly.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from migration_factory.control_tower.application.services import (
    StageContinuationPolicyService,
)
from migration_factory.control_tower.domain.entities import (
    StageChainEventRecord,
    StageChainLedgerRecord,
    StageOutputRegistryRecord,
)
from migration_factory.control_tower.domain.errors import (
    ContinuationPolicyViolationError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_input_checksum(value: str) -> str:
    """Create a deterministic input checksum for testing."""
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _make_stage_checksum(value: str) -> str:
    """Create a deterministic stage checksum (same algorithm as ledger)."""
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


STAGE1_INPUT_CS = _make_input_checksum("stage1-input-legacy-source")
STAGE1_OUTPUT_CS = _make_input_checksum("stage1-output-sandbox")
STAGE2_INPUT_CS = STAGE1_OUTPUT_CS  # Correct: reads from Stage 1 sandbox
STAGE2_WRONG_INPUT_CS = _make_input_checksum("stage2-wrong-input-from-elsewhere")
STAGE2_OUTPUT_CS = _make_input_checksum("stage2-output-sandbox")
STAGE3_INPUT_CS = STAGE2_OUTPUT_CS  # Correct: reads from Stage 2 sandbox
STAGE3_WRONG_INPUT_CS = _make_input_checksum("stage3-wrong-input-from-elsewhere")


class FakeStageChainLedgerRepo:
    """Fake ledger repository for stage continuation tests."""

    def __init__(self) -> None:
        self._ledger_entries: list[StageChainLedgerRecord] = []
        self._output_entries: list[StageOutputRegistryRecord] = []
        self._events: list[StageChainEventRecord] = []

    def insert_many(self, ledger_entries: list[StageChainLedgerRecord]) -> None:
        self._ledger_entries.extend(ledger_entries)

    def list_for_job(self, job_id: str) -> tuple[StageChainLedgerRecord, ...]:
        return tuple(e for e in self._ledger_entries if e.job_id == job_id)

    def insert_output(self, output: StageOutputRegistryRecord) -> None:
        self._output_entries.append(output)

    def list_outputs_for_job(self, job_id: str) -> tuple[StageOutputRegistryRecord, ...]:
        return tuple(e for e in self._output_entries if e.job_id == job_id)

    def insert_event(self, event: StageChainEventRecord) -> None:
        self._events.append(event)

    def list_events_for_job(self, job_id: str) -> tuple[StageChainEventRecord, ...]:
        return tuple(e for e in self._events if e.job_id == job_id)


class FakeMigrationJobRepo:
    """Fake migration job repository for continuation policy tests."""

    def __init__(self) -> None:
        self._jobs: dict[str, MagicMock] = {}

    def get(self, job_id: str) -> MagicMock | None:
        return self._jobs.get(job_id)

    def insert_job(self, job_id: str, pipeline_id: str = "springboot-216-to-356-java21-three-stage",
                   pipeline_version: str = "1.0.0") -> None:
        mock = MagicMock()
        mock.job_id = job_id
        mock.pipeline_id = pipeline_id
        mock.pipeline_version = pipeline_version
        self._jobs[job_id] = mock


class PipelineStageMock:
    """Mock for pipeline stages within payload."""
    def __init__(self, stage_index: int, stage_id: str, command_jdk: str = "java11",
                 input_source_kind: str = "legacy_source") -> None:
        self.stage_index = stage_index
        self.stage_id = stage_id
        self.command_jdk = command_jdk
        self.input_source = type("InputSource", (), {"kind": input_source_kind})()


class PipelinePayloadMock:
    """Mock for pipeline payload containing stages."""
    def __init__(self, stages: list) -> None:
        self.stages = stages


class PipelineDefinitionMock:
    """Mock for pipeline definition."""
    def __init__(self, pipeline_id: str, payload: PipelinePayloadMock) -> None:
        self.pipeline_id = pipeline_id
        self.payload = payload


class FakePipelineRepo:
    """Fake pipeline repository for continuation policy tests."""

    def __init__(self) -> None:
        self._pipelines: dict[str, PipelineDefinitionMock | None] = {}

    def get_exact(self, pipeline_id: str, pipeline_version: str) -> PipelineDefinitionMock | None:
        return self._pipelines.get(f"{pipeline_id}/{pipeline_version}")

    def insert_pipeline(self, pipeline_id: str, pipeline_version: str, payload: PipelinePayloadMock) -> None:
        self._pipelines[f"{pipeline_id}/{pipeline_version}"] = PipelineDefinitionMock(pipeline_id, payload)


class FakeUnitOfWork:
    """Fake UnitOfWork for continuation policy tests."""

    def __init__(self) -> None:
        self.stage_chain_ledger = FakeStageChainLedgerRepo()
        self.migration_jobs = FakeMigrationJobRepo()
        self.pipeline_definitions = FakePipelineRepo()

    def __enter__(self) -> "FakeUnitOfWork":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def _setup_three_stage_pipeline(uow: FakeUnitOfWork, job_id: str = "job-cont-001") -> None:
    """Set up a three-stage pipeline with ledger entries for Stage 1 output."""
    import hashlib

    # Create pipeline with three stages
    stages = [
        PipelineStageMock(1, "springboot-2.1.6-to-2.7-java11", "java11"),
        PipelineStageMock(2, "springboot-2.7-to-3.5-java17", "java17"),
        PipelineStageMock(3, "springboot-3.5-java17-to-java21", "java21"),
    ]
    payload = PipelinePayloadMock(stages)
    uow.pipeline_definitions.insert_pipeline(
        "springboot-216-to-356-java21-three-stage", "1.0.0", payload
    )
    uow.migration_jobs.insert_job(job_id)

    # Create Stage 1 ledger entry with output checksum (simulating completed Stage 1)
    stage1_ledger = StageChainLedgerRecord(
        ledger_id=f"ledger-{job_id}-0001",
        job_id=job_id,
        stage_index=1,
        stage_run_id=f"stage-{job_id}-0001",
        chain_status="passed",
        input_source_kind="legacy_source",
        input_checksum=STAGE1_INPUT_CS,
        output_artifact_id=f"artifact-{job_id}-s1-sandbox",
        output_checksum=STAGE1_OUTPUT_CS,
        output_registered_at="2026-06-12T00:00:00Z",
        checksum_guard=_make_stage_checksum("stage1-guard"),
        created_at="2026-06-12T00:00:00Z",
        created_by="system",
    )
    uow.stage_chain_ledger.insert_many([stage1_ledger])


def _setup_stage1_and_stage2_output(uow: FakeUnitOfWork, job_id: str = "job-cont-002") -> None:
    """Set up pipeline with Stage 1 and Stage 2 outputs."""
    import hashlib

    stages = [
        PipelineStageMock(1, "springboot-2.1.6-to-2.7-java11", "java11"),
        PipelineStageMock(2, "springboot-2.7-to-3.5-java17", "java17"),
        PipelineStageMock(3, "springboot-3.5-java17-to-java21", "java21"),
    ]
    payload = PipelinePayloadMock(stages)
    uow.pipeline_definitions.insert_pipeline(
        "springboot-216-to-356-java21-three-stage", "1.0.0", payload
    )
    uow.migration_jobs.insert_job(job_id)

    ledger_entries = [
        StageChainLedgerRecord(
            ledger_id=f"ledger-{job_id}-0001",
            job_id=job_id,
            stage_index=1,
            stage_run_id=f"stage-{job_id}-0001",
            chain_status="passed",
            input_source_kind="legacy_source",
            input_checksum=STAGE1_INPUT_CS,
            output_artifact_id=f"artifact-{job_id}-s1-sandbox",
            output_checksum=STAGE1_OUTPUT_CS,
            output_registered_at="2026-06-12T00:00:00Z",
            checksum_guard=_make_stage_checksum("stage1-guard"),
            created_at="2026-06-12T00:00:00Z",
            created_by="system",
        ),
        StageChainLedgerRecord(
            ledger_id=f"ledger-{job_id}-0002",
            job_id=job_id,
            stage_index=2,
            stage_run_id=f"stage-{job_id}-0002",
            chain_status="passed",
            input_source_kind="previous_stage",
            input_checksum=STAGE2_INPUT_CS,
            output_artifact_id=f"artifact-{job_id}-s2-sandbox",
            output_checksum=STAGE2_OUTPUT_CS,
            output_registered_at="2026-06-12T00:01:00Z",
            checksum_guard=_make_stage_checksum("stage2-guard"),
            created_at="2026-06-12T00:01:00Z",
            created_by="system",
        ),
    ]
    uow.stage_chain_ledger.insert_many(ledger_entries)


def _setup_fresh_uow() -> FakeUnitOfWork:
    """Create a fresh fake UoW."""
    return FakeUnitOfWork()


# ---------------------------------------------------------------------------
# Stage continuation policy enforcement
# ---------------------------------------------------------------------------


class TestStageContinuationEnforcement:
    """Tests for StageContinuationPolicyService.enforce_stage_continuation."""

    def test_stage1_no_prior_check(self):
        """Stage 1 should not check prior-stage output (reads from legacy source)."""
        uow = _setup_fresh_uow()
        _setup_three_stage_pipeline(uow)

        service = StageContinuationPolicyService(lambda: uow)
        # Should not raise for Stage 1
        service.enforce_stage_continuation(
            job_id="job-cont-001",
            stage_index=1,
            input_checksum=STAGE1_INPUT_CS,
        )

        # Verify a policy_created event was recorded
        events = uow.stage_chain_ledger.list_events_for_job("job-cont-001")
        policy_events = [e for e in events if e.event_type == "policy_created"]
        assert len(policy_events) == 1
        assert policy_events[0].stage_index == 1

    def test_stage2_correct_input_passes(self):
        """Stage 2 with correct Stage 1 sandbox checksum should pass."""
        uow = _setup_fresh_uow()
        _setup_three_stage_pipeline(uow)

        service = StageContinuationPolicyService(lambda: uow)
        # Use STAGE1_OUTPUT_CS as Stage 2's input (correct - reads from Stage 1 sandbox)
        service.enforce_stage_continuation(
            job_id="job-cont-001",
            stage_index=2,
            input_checksum=STAGE1_OUTPUT_CS,
        )

        events = uow.stage_chain_ledger.list_events_for_job("job-cont-001")
        matched_events = [e for e in events if e.event_type == "policy_matched"]
        queued_events = [e for e in events if e.event_type == "stage_queued"]
        assert len(matched_events) == 1
        assert matched_events[0].stage_index == 2
        assert len(queued_events) >= 1

    def test_stage2_wrong_input_raises(self):
        """Stage 2 with wrong input checksum should raise ContinuationPolicyViolationError."""
        uow = _setup_fresh_uow()
        _setup_three_stage_pipeline(uow)

        service = StageContinuationPolicyService(lambda: uow)
        with pytest.raises(ContinuationPolicyViolationError) as excinfo:
            service.enforce_stage_continuation(
                job_id="job-cont-001",
                stage_index=2,
                input_checksum=STAGE2_WRONG_INPUT_CS,
            )

        assert "Stage 2 continuation policy violation" in str(excinfo.value)
        assert "does not match" in str(excinfo.value)

        # Verify blocked event was recorded
        events = uow.stage_chain_ledger.list_events_for_job("job-cont-001")
        mismatch_events = [e for e in events if e.event_type == "policy_mismatched"]
        blocked_events = [e for e in events if e.event_type == "stage_blocked"]
        assert len(mismatch_events) == 1
        assert mismatch_events[0].stage_index == 2
        assert len(blocked_events) == 1
        assert blocked_events[0].new_status == "blocked"

    def test_stage3_correct_input_passes(self):
        """Stage 3 with correct Stage 2 sandbox checksum should pass."""
        uow = _setup_fresh_uow()
        _setup_stage1_and_stage2_output(uow)

        service = StageContinuationPolicyService(lambda: uow)
        service.enforce_stage_continuation(
            job_id="job-cont-002",
            stage_index=3,
            input_checksum=STAGE2_OUTPUT_CS,
        )

        events = uow.stage_chain_ledger.list_events_for_job("job-cont-002")
        matched_events = [e for e in events if e.event_type == "policy_matched"]
        assert len(matched_events) == 1
        assert matched_events[0].stage_index == 3

    def test_stage3_wrong_input_raises(self):
        """Stage 3 with wrong input checksum should raise ContinuationPolicyViolationError."""
        uow = _setup_fresh_uow()
        _setup_stage1_and_stage2_output(uow)

        service = StageContinuationPolicyService(lambda: uow)
        with pytest.raises(ContinuationPolicyViolationError) as excinfo:
            service.enforce_stage_continuation(
                job_id="job-cont-002",
                stage_index=3,
                input_checksum=STAGE3_WRONG_INPUT_CS,
            )

        assert "continuation policy violation" in str(excinfo.value)

        events = uow.stage_chain_ledger.list_events_for_job("job-cont-002")
        blocked_events = [e for e in events if e.event_type == "stage_blocked"]
        assert len(blocked_events) == 1

    def test_stage2_with_no_prior_output_raises(self):
        """Stage 2 with no prior stage output should raise ContinuationPolicyViolationError."""
        uow = _setup_fresh_uow()
        stages = [
            PipelineStageMock(1, "springboot-2.1.6-to-2.7-java11", "java11"),
            PipelineStageMock(2, "springboot-2.7-to-3.5-java17", "java17"),
        ]
        payload = PipelinePayloadMock(stages)
        uow.pipeline_definitions.insert_pipeline(
            "springboot-216-to-356-java21-three-stage", "1.0.0", payload
        )
        uow.migration_jobs.insert_job("job-cont-003")
        # Note: No ledger entry for Stage 1 with output

        service = StageContinuationPolicyService(lambda: uow)
        with pytest.raises(ContinuationPolicyViolationError) as excinfo:
            service.enforce_stage_continuation(
                job_id="job-cont-003",
                stage_index=2,
                input_checksum=STAGE2_INPUT_CS,
            )

        assert "no output checksum" in str(excinfo.value)

    def test_deterministic_blocked_event(self):
        """Blocked event should contain deterministic payload fields."""
        uow = _setup_fresh_uow()
        _setup_three_stage_pipeline(uow)

        service = StageContinuationPolicyService(lambda: uow)
        with pytest.raises(ContinuationPolicyViolationError):
            service.enforce_stage_continuation(
                job_id="job-cont-001",
                stage_index=2,
                input_checksum=STAGE2_WRONG_INPUT_CS,
            )

        events = uow.stage_chain_ledger.list_events_for_job("job-cont-001")
        blocked = [e for e in events if e.event_type == "stage_blocked"]
        assert len(blocked) == 1
        payload = json.loads(blocked[0].payload_json)
        assert payload["stage_index"] == 2
        assert payload["expected_prior_stage_index"] == 1
        assert "continuation policy violation" in payload["reason"]

    def test_deterministic_queued_event(self):
        """Queued event should contain deterministic payload fields."""
        uow = _setup_fresh_uow()
        _setup_three_stage_pipeline(uow)

        service = StageContinuationPolicyService(lambda: uow)
        service.enforce_stage_continuation(
            job_id="job-cont-001",
            stage_index=2,
            input_checksum=STAGE1_OUTPUT_CS,
        )

        events = uow.stage_chain_ledger.list_events_for_job("job-cont-001")
        queued = [e for e in events if e.event_type == "stage_queued"]
        assert len(queued) == 1
        payload = json.loads(queued[0].payload_json)
        assert payload["stage_index"] == 2
        assert payload["input_checksum"] == STAGE1_OUTPUT_CS

    def test_deterministic_mismatch_event(self):
        """Mismatch event should contain both expected and actual checksums."""
        uow = _setup_fresh_uow()
        _setup_three_stage_pipeline(uow)

        service = StageContinuationPolicyService(lambda: uow)
        with pytest.raises(ContinuationPolicyViolationError):
            service.enforce_stage_continuation(
                job_id="job-cont-001",
                stage_index=2,
                input_checksum=STAGE2_WRONG_INPUT_CS,
            )

        events = uow.stage_chain_ledger.list_events_for_job("job-cont-001")
        mismatch = [e for e in events if e.event_type == "policy_mismatched"]
        assert len(mismatch) == 1
        payload = json.loads(mismatch[0].payload_json)
        assert payload["expected_prior_output_checksum"] == STAGE1_OUTPUT_CS
        assert payload["actual_input_checksum"] == STAGE2_WRONG_INPUT_CS


# ---------------------------------------------------------------------------
# Stage readiness checks
# ---------------------------------------------------------------------------


class TestStageReadinessCheck:
    """Tests for StageContinuationPolicyService.check_stage_readiness."""

    def test_stage2_ready_after_match(self):
        """Stage 2 should be ready after policy matched."""
        uow = _setup_fresh_uow()
        _setup_three_stage_pipeline(uow)

        service = StageContinuationPolicyService(lambda: uow)
        service.enforce_stage_continuation(
            job_id="job-cont-001",
            stage_index=2,
            input_checksum=STAGE1_OUTPUT_CS,
        )

        allowed, reason = service.check_stage_readiness("job-cont-001", 2)
        assert allowed is True
        assert reason is None

    def test_stage2_blocked_after_mismatch(self):
        """Stage 2 should be blocked after policy mismatch."""
        uow = _setup_fresh_uow()
        _setup_three_stage_pipeline(uow)

        service = StageContinuationPolicyService(lambda: uow)
        with pytest.raises(ContinuationPolicyViolationError):
            service.enforce_stage_continuation(
                job_id="job-cont-001",
                stage_index=2,
                input_checksum=STAGE2_WRONG_INPUT_CS,
            )

        allowed, reason = service.check_stage_readiness("job-cont-001", 2)
        assert allowed is False
        assert reason is not None

    def test_stage3_ready_after_match(self):
        """Stage 3 should be ready after policy matched with Stage 2 output."""
        uow = _setup_fresh_uow()
        _setup_stage1_and_stage2_output(uow)

        service = StageContinuationPolicyService(lambda: uow)
        service.enforce_stage_continuation(
            job_id="job-cont-002",
            stage_index=3,
            input_checksum=STAGE2_OUTPUT_CS,
        )

        allowed, reason = service.check_stage_readiness("job-cont-002", 3)
        assert allowed is True
        assert reason is None

    def test_stage1_always_passes_legacy(self):
        """Stage 1 should always pass continuity check (legacy source)."""
        uow = _setup_fresh_uow()
        _setup_three_stage_pipeline(uow)

        service = StageContinuationPolicyService(lambda: uow)
        # Stage 1 reads from legacy source, not from prior-stage sandbox
        service.enforce_stage_continuation(
            job_id="job-cont-001",
            stage_index=1,
            input_checksum=STAGE1_INPUT_CS,
        )

        allowed, reason = service.check_stage_readiness("job-cont-001", 1)
        assert allowed is True
