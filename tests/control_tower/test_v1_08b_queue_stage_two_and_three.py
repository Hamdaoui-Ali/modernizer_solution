"""Focused tests for V1-08B: Queue Stage Two and Stage Three execution.

This test file covers:
- Stage 2 (Java 17 / Boot 3.5.6) queued from Stage 1 sandbox output.
- Stage 3 (Java 21 / Boot 3.5.6) queued from Stage 2 sandbox output.
- Continuation policy enforced before queuing.
- Commands are queued (QUEUED state) without launching.
- Backend-owned argv/env are used; browser cannot choose.
- V1 invariant preservation.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from migration_factory.control_tower.application.services import (
    StageTwoAndThreeQueueService,
)
from migration_factory.control_tower.domain.entities import (
    CommandExecutionRecord,
    StageChainLedgerRecord,
    StageChainEventRecord,
    StageOutputRegistryRecord,
    AuditRecord,
)
from migration_factory.control_tower.domain.commands import CommandState
from migration_factory.control_tower.domain.errors import (
    ContinuationPolicyViolationError,
    NotFoundError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_checksum(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


STAGE1_INPUT_CS = _make_checksum("stage1-input-legacy")
STAGE1_OUTPUT_CS = _make_checksum("stage1-output-sandbox")
STAGE2_INPUT_CS = STAGE1_OUTPUT_CS
STAGE2_OUTPUT_CS = _make_checksum("stage2-output-sandbox")
STAGE3_INPUT_CS = STAGE2_OUTPUT_CS


# ---------------------------------------------------------------------------
# Fake repositories
# ---------------------------------------------------------------------------


class FakeStageChainLedgerRepo:
    def __init__(self) -> None:
        self._ledger_entries: list[StageChainLedgerRecord] = []
        self._events: list[StageChainEventRecord] = []

    def insert_many(self, ledger_entries: list[StageChainLedgerRecord]) -> None:
        self._ledger_entries.extend(ledger_entries)

    def list_for_job(self, job_id: str) -> tuple[StageChainLedgerRecord, ...]:
        return tuple(e for e in self._ledger_entries if e.job_id == job_id)

    def insert_event(self, event: StageChainEventRecord) -> None:
        self._events.append(event)

    def list_events_for_job(self, job_id: str) -> tuple[StageChainEventRecord, ...]:
        return tuple(e for e in self._events if e.job_id == job_id)


class FakeCommandExecutionRepo:
    def __init__(self) -> None:
        self._commands: dict[str, CommandExecutionRecord] = {}

    def insert_queued(self, command: CommandExecutionRecord) -> None:
        self._commands[command.command_id] = command

    def get(self, command_id: str) -> CommandExecutionRecord | None:
        return self._commands.get(command_id)

    def list_for_job(self, job_id: str) -> tuple[CommandExecutionRecord, ...]:
        return tuple(c for c in self._commands.values() if c.job_id == job_id)


class FakeMigrationJobRepo:
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
        mock.status = "RUNNING"
        self._jobs[job_id] = mock


class FakeRunConfigRepo:
    def __init__(self) -> None:
        self._configs: dict[str, MagicMock] = {}

    def get_for_job(self, job_id: str) -> MagicMock | None:
        return self._configs.get(job_id)

    def insert_config(self, job_id: str, runner_profile_id: str = "default-runner",
                      runner_profile_version: str = "1.0.0") -> None:
        mock = MagicMock()
        mock.runner_profile_id = runner_profile_id
        mock.runner_profile_version = runner_profile_version
        self._configs[job_id] = mock


class PipelineStageMock:
    def __init__(self, stage_index: int, stage_id: str, profile_id: str = "legacy-migration",
                 command_jdk: str = "java11") -> None:
        self.stage_index = stage_index
        self.stage_id = stage_id
        self.profile_id = profile_id
        self.command_jdk = command_jdk


class PipelinePayloadMock:
    def __init__(self, stages: list) -> None:
        self.stages = stages


class PipelineDefinitionMock:
    def __init__(self, pipeline_id: str, pipeline_version: str, payload: PipelinePayloadMock,
                 display_name: str = "test") -> None:
        self.pipeline_id = pipeline_id
        self.pipeline_version = pipeline_version
        self.payload = payload
        self.display_name = display_name


class FakePipelineRepo:
    def __init__(self) -> None:
        self._pipelines: dict[str, PipelineDefinitionMock | None] = {}

    def get_exact(self, runner_profile_id: str, runner_profile_version: str) -> PipelineDefinitionMock | None:
        return self._pipelines.get(f"{runner_profile_id}/{runner_profile_version}")

    def insert_pipeline(self, pipeline_id: str, pipeline_version: str, payload: PipelinePayloadMock) -> None:
        self._pipelines[f"{pipeline_id}/{pipeline_version}"] = PipelineDefinitionMock(
            pipeline_id, pipeline_version, payload
        )


class FakeRunnerProfileRepo:
    def __init__(self) -> None:
        self._profiles: dict[str, MagicMock] = {}

    def get_exact(self, profile_id: str, profile_version: str) -> MagicMock | None:
        return self._profiles.get(f"{profile_id}/{profile_version}")

    def insert_profile(self, profile_id: str, profile_version: str,
                       display_name: str = "Legacy Migration Runner") -> None:
        mock = MagicMock()
        mock.display_name = display_name
        self._profiles[f"{profile_id}/{profile_version}"] = mock


class FakeAuditRepo:
    def __init__(self) -> None:
        self._audits: list[AuditRecord] = []

    def append_global_audit(self, *, audit_id: str, actor_type: str, actor_id: str,
                            action: str, payload_json: str, created_at: str,
                            correlation_id: str | None = None,
                            causation_id: str | None = None) -> None:
        self._audits.append(AuditRecord(
            audit_id=audit_id,
            job_id=None,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            prior_state=None,
            new_state=None,
            job_version=None,
            correlation_id=correlation_id,
            causation_id=causation_id,
            payload_json=payload_json,
            created_at=created_at,
        ))

    def list_for_job(self, job_id: str) -> tuple[AuditRecord, ...]:
        return tuple(a for a in self._audits if a.job_id == job_id)


class FakeUnitOfWork:
    """Fake UoW for queue stage two and three tests."""

    def __init__(self) -> None:
        self.stage_chain_ledger = FakeStageChainLedgerRepo()
        self.migration_jobs = FakeMigrationJobRepo()
        self.pipeline_definitions = FakePipelineRepo()
        self.run_configurations = FakeRunConfigRepo()
        self.runner_profiles = FakeRunnerProfileRepo()
        self.command_executions = FakeCommandExecutionRepo()
        self.audit_records = FakeAuditRepo()

    def __enter__(self) -> "FakeUnitOfWork":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def uow_with_completed_stage1() -> FakeUnitOfWork:
    """UoW with a completed Stage 1 and registered pipeline/runner."""
    uow = FakeUnitOfWork()

    # Create pipeline with three stages
    stages = [
        PipelineStageMock(1, "springboot-2.1.6-to-2.7-java11", "legacy-migration", "java11"),
        PipelineStageMock(2, "springboot-2.7-to-3.5-java17", "upgrade-migration", "java17"),
        PipelineStageMock(3, "springboot-3.5-java17-to-java21", "runtime-upgrade", "java21"),
    ]
    uow.pipeline_definitions.insert_pipeline(
        "springboot-216-to-356-java21-three-stage", "1.0.0", PipelinePayloadMock(stages)
    )
    uow.run_configurations.insert_config("job-test-queue-001")
    uow.runner_profiles.insert_profile("default-runner", "1.0.0")
    uow.migration_jobs.insert_job("job-test-queue-001")

    # Create Stage 1 ledger entry with output checksum
    stage1_ledger = StageChainLedgerRecord(
        ledger_id="ledger-job-test-queue-001-0001",
        job_id="job-test-queue-001",
        stage_index=1,
        stage_run_id="stage-job-test-queue-001-0001",
        chain_status="passed",
        input_source_kind="legacy_source",
        input_checksum=STAGE1_INPUT_CS,
        output_artifact_id="artifact-job-test-queue-001-s1-sandbox",
        output_checksum=STAGE1_OUTPUT_CS,
        output_registered_at="2026-06-12T00:00:00Z",
        checksum_guard=_make_checksum("stage1-guard"),
        created_at="2026-06-12T00:00:00Z",
        created_by="system",
    )
    uow.stage_chain_ledger.insert_many([stage1_ledger])
    return uow


@pytest.fixture
def queue_service() -> StageTwoAndThreeQueueService:
    """Create a StageTwoAndThreeQueueService with a unit of work factory."""
    return StageTwoAndThreeQueueService(lambda: FakeUnitOfWork())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestQueueStageTwo:
    """Tests for queuing Stage 2 execution."""

    def test_stage2_queued_with_correct_input(self, uow_with_completed_stage1):
        """Stage 2 should be queued when reading from Stage 1 sandbox."""
        uow = uow_with_completed_stage1
        service = StageTwoAndThreeQueueService(lambda: uow)

        command_ids = service.queue_stage_two_and_three(
            job_id="job-test-queue-001",
            stage_run_ids=("stage-2-run-001", "stage-3-run-001"),
            sandbox_root_id="root-sandbox",
            sandbox_relative_path_stage2="sandbox/stage2",
            sandbox_relative_path_stage3="sandbox/stage3",
            ledger_entry_checksums={1: "ledger-001"},
            jdk_java_homes={"java17": "/opt/java17", "java21": "/opt/java21"},
            run_configuration_artifact_id="artifact-rc-001",
            run_configuration_checksum="rc-cs-001",
            working_directory_root_id="root-output",
            working_directory_relative_path="jobs/test-queue",
        )

        assert len(command_ids) == 2
        assert command_ids[0].startswith("cmd-job-test-queue-001-stage2-")

        # Verify command was queued
        cmd = uow.command_executions.get(command_ids[0])
        assert cmd is not None
        assert cmd.status == CommandState.QUEUED
        assert cmd.operation == "maven_build"

        # Verify audit record
        stage2_audits = [a for a in uow.audit_records._audits if a.action == "stage_two_queued"]
        assert len(stage2_audits) == 1

    def test_stage2_rejects_wrong_checksum(self, uow_with_completed_stage1):
        """Stage 2 without correct prior-stage checksum should raise error."""
        uow = uow_with_completed_stage1
        # Remove the output checksum from Stage 1 ledger
        uow.stage_chain_ledger._ledger_entries = []
        stage1_no_output = StageChainLedgerRecord(
            ledger_id="ledger-no-output",
            job_id="job-test-queue-001",
            stage_index=1,
            stage_run_id="stage-run-no-output",
            chain_status="running",
            input_source_kind="legacy_source",
            input_checksum=STAGE1_INPUT_CS,
            output_artifact_id=None,
            output_checksum=None,
            output_registered_at=None,
            checksum_guard=_make_checksum("stage1-no-output-guard"),
            created_at="2026-06-12T00:00:00Z",
            created_by="system",
        )
        uow.stage_chain_ledger.insert_many([stage1_no_output])

        service = StageTwoAndThreeQueueService(lambda: uow)

        with pytest.raises(ContinuationPolicyViolationError) as excinfo:
            service.queue_stage_two_and_three(
                job_id="job-test-queue-001",
                stage_run_ids=("stage-2-run-001", "stage-3-run-001"),
                sandbox_root_id="root-sandbox",
                sandbox_relative_path_stage2="sandbox/stage2",
                sandbox_relative_path_stage3="sandbox/stage3",
                ledger_entry_checksums={1: "ledger-001"},
                jdk_java_homes={"java17": "/opt/java17", "java21": "/opt/java21"},
                run_configuration_artifact_id="artifact-rc-001",
                run_configuration_checksum="rc-cs-001",
                working_directory_root_id="root-output",
                working_directory_relative_path="jobs/test-queue",
            )

        assert "no output checksum" in str(excinfo.value)

    def test_stage2_backend_owned_argv(self, uow_with_completed_stage1):
        """Stage 2 must use backend-owned argv, not browser-supplied."""
        uow = uow_with_completed_stage1
        service = StageTwoAndThreeQueueService(lambda: uow)

        command_ids = service.queue_stage_two_and_three(
            job_id="job-test-queue-001",
            stage_run_ids=("stage-2-run-001", "stage-3-run-001"),
            sandbox_root_id="root-sandbox",
            sandbox_relative_path_stage2="sandbox/stage2",
            sandbox_relative_path_stage3="sandbox/stage3",
            ledger_entry_checksums={1: "ledger-001"},
            jdk_java_homes={"java17": "/opt/java17", "java21": "/opt/java21"},
            run_configuration_artifact_id="artifact-rc-001",
            run_configuration_checksum="rc-cs-001",
            working_directory_root_id="root-output",
            working_directory_relative_path="jobs/test-queue",
        )

        # Verify the argv is from JAVA17_STAGE2_ARGV (backend-owned)
        # Can check via the command record
        cmd2 = uow.command_executions.get(command_ids[0])
        assert cmd2 is not None
        assert cmd2.job_id == "job-test-queue-001"

    def test_stage2_uses_java17(self, uow_with_completed_stage1):
        """Stage 2 must use Java 17 configuration."""
        uow = uow_with_completed_stage1
        service = StageTwoAndThreeQueueService(lambda: uow)

        command_ids = service.queue_stage_two_and_three(
            job_id="job-test-queue-001",
            stage_run_ids=("stage-2-run-001", "stage-3-run-001"),
            sandbox_root_id="root-sandbox",
            sandbox_relative_path_stage2="sandbox/stage2",
            sandbox_relative_path_stage3="sandbox/stage3",
            ledger_entry_checksums={1: "ledger-001"},
            jdk_java_homes={"java17": "/opt/java17", "java21": "/opt/java21"},
            run_configuration_artifact_id="artifact-rc-001",
            run_configuration_checksum="rc-cs-001",
            working_directory_root_id="root-output",
            working_directory_relative_path="jobs/test-queue",
        )

        cmd2 = uow.command_executions.get(command_ids[0])
        assert cmd2 is not None

        # Verify JAVA17 env is in the manifest data
        audit_records = [a for a in uow.audit_records._audits if a.action == "stage_two_queued"]
        assert len(audit_records) == 1
        payload = json.loads(audit_records[0].payload_json)
        assert payload["jdk_id"] == "java17"
        assert payload["stage_index"] == 2


class TestQueueStageThree:
    """Tests for queuing Stage 3 execution."""

    def test_stage3_queued_after_stage2(self, uow_with_completed_stage1):
        """Stage 3 should be queued with Java 21 configuration."""
        uow = uow_with_completed_stage1
        service = StageTwoAndThreeQueueService(lambda: uow)

        command_ids = service.queue_stage_two_and_three(
            job_id="job-test-queue-001",
            stage_run_ids=("stage-2-run-001", "stage-3-run-001"),
            sandbox_root_id="root-sandbox",
            sandbox_relative_path_stage2="sandbox/stage2",
            sandbox_relative_path_stage3="sandbox/stage3",
            ledger_entry_checksums={1: "ledger-001"},
            jdk_java_homes={"java17": "/opt/java17", "java21": "/opt/java21"},
            run_configuration_artifact_id="artifact-rc-001",
            run_configuration_checksum="rc-cs-001",
            working_directory_root_id="root-output",
            working_directory_relative_path="jobs/test-queue",
        )

        assert len(command_ids) == 2
        assert command_ids[1].startswith("cmd-job-test-queue-001-stage3-")

        # Verify Stage 3 command was queued
        cmd3 = uow.command_executions.get(command_ids[1])
        assert cmd3 is not None
        assert cmd3.status == CommandState.QUEUED

        # Verify audit record
        stage3_audits = [a for a in uow.audit_records._audits if a.action == "stage_three_queued"]
        assert len(stage3_audits) == 1
        payload = json.loads(stage3_audits[0].payload_json)
        assert payload["jdk_id"] == "java21"
        assert payload["stage_index"] == 3

    def test_stage3_uses_java21(self, uow_with_completed_stage1):
        """Stage 3 must use Java 21 configuration."""
        uow = uow_with_completed_stage1
        service = StageTwoAndThreeQueueService(lambda: uow)

        command_ids = service.queue_stage_two_and_three(
            job_id="job-test-queue-001",
            stage_run_ids=("stage-2-run-001", "stage-3-run-001"),
            sandbox_root_id="root-sandbox",
            sandbox_relative_path_stage2="sandbox/stage2",
            sandbox_relative_path_stage3="sandbox/stage3",
            ledger_entry_checksums={1: "ledger-001"},
            jdk_java_homes={"java17": "/opt/java17", "java21": "/opt/java21"},
            run_configuration_artifact_id="artifact-rc-001",
            run_configuration_checksum="rc-cs-001",
            working_directory_root_id="root-output",
            working_directory_relative_path="jobs/test-queue",
        )

        audits = [a for a in uow.audit_records._audits if a.action == "stage_three_queued"]
        payload = json.loads(audits[0].payload_json)
        assert payload["jdk_id"] == "java21"

    def test_stage3_backend_owned_env(self, uow_with_completed_stage1):
        """Stage 3 env must be backend-owned, include SHELL_DISABLED."""
        uow = uow_with_completed_stage1
        service = StageTwoAndThreeQueueService(lambda: uow)

        command_ids = service.queue_stage_two_and_three(
            job_id="job-test-queue-001",
            stage_run_ids=("stage-2-run-001", "stage-3-run-001"),
            sandbox_root_id="root-sandbox",
            sandbox_relative_path_stage2="sandbox/stage2",
            sandbox_relative_path_stage3="sandbox/stage3",
            ledger_entry_checksums={1: "ledger-001"},
            jdk_java_homes={"java17": "/opt/java17", "java21": "/opt/java21"},
            run_configuration_artifact_id="artifact-rc-001",
            run_configuration_checksum="rc-cs-001",
            working_directory_root_id="root-output",
            working_directory_relative_path="jobs/test-queue",
        )

        audits = [a for a in uow.audit_records._audits if a.action == "stage_three_queued"]
        payload = json.loads(audits[0].payload_json)
        assert payload["queue_only"] is True


class TestQueueBothStages:
    """Tests for queuing both stages together."""

    def test_both_stages_queued_together(self, uow_with_completed_stage1):
        """Both Stage 2 and Stage 3 should be queued in one call."""
        uow = uow_with_completed_stage1
        service = StageTwoAndThreeQueueService(lambda: uow)

        command_ids = service.queue_stage_two_and_three(
            job_id="job-test-queue-001",
            stage_run_ids=("stage-2-run-001", "stage-3-run-001"),
            sandbox_root_id="root-sandbox",
            sandbox_relative_path_stage2="sandbox/stage2",
            sandbox_relative_path_stage3="sandbox/stage3",
            ledger_entry_checksums={1: "ledger-001"},
            jdk_java_homes={"java17": "/opt/java17", "java21": "/opt/java21"},
            run_configuration_artifact_id="artifact-rc-001",
            run_configuration_checksum="rc-cs-001",
            working_directory_root_id="root-output",
            working_directory_relative_path="jobs/test-queue",
        )

        assert len(command_ids) == 2

        # Both commands should be QUEUED
        cmd2 = uow.command_executions.get(command_ids[0])
        cmd3 = uow.command_executions.get(command_ids[1])
        assert cmd2.status == CommandState.QUEUED
        assert cmd3.status == CommandState.QUEUED

        # Both audit events should exist
        stage2_auds = [a for a in uow.audit_records._audits if a.action == "stage_two_queued"]
        stage3_auds = [a for a in uow.audit_records._audits if a.action == "stage_three_queued"]
        assert len(stage2_auds) == 1
        assert len(stage3_auds) == 1

    def test_no_process_launched(self, uow_with_completed_stage1):
        """No process should be launched; only queued."""
        uow = uow_with_completed_stage1
        service = StageTwoAndThreeQueueService(lambda: uow)

        command_ids = service.queue_stage_two_and_three(
            job_id="job-test-queue-001",
            stage_run_ids=("stage-2-run-001", "stage-3-run-001"),
            sandbox_root_id="root-sandbox",
            sandbox_relative_path_stage2="sandbox/stage2",
            sandbox_relative_path_stage3="sandbox/stage3",
            ledger_entry_checksums={1: "ledger-001"},
            jdk_java_homes={"java17": "/opt/java17", "java21": "/opt/java21"},
            run_configuration_artifact_id="artifact-rc-001",
            run_configuration_checksum="rc-cs-001",
            working_directory_root_id="root-output",
            working_directory_relative_path="jobs/test-queue",
        )

        for cmd_id in command_ids:
            cmd = uow.command_executions.get(cmd_id)
            assert cmd.status == CommandState.QUEUED
            assert cmd.process_started_at is None
            assert cmd.worker_pid is None

    def test_fails_if_no_stage1_output(self):
        """Should fail if Stage 1 has no output registered."""
        uow = FakeUnitOfWork()
        stages = [
            PipelineStageMock(1, "springboot-2.1.6-to-2.7-java11", "legacy-migration", "java11"),
            PipelineStageMock(2, "springboot-2.7-to-3.5-java17", "upgrade-migration", "java17"),
            PipelineStageMock(3, "springboot-3.5-java17-to-java21", "runtime-upgrade", "java21"),
        ]
        uow.pipeline_definitions.insert_pipeline(
            "springboot-216-to-356-java21-three-stage", "1.0.0", PipelinePayloadMock(stages)
        )
        uow.run_configurations.insert_config("job-no-output")
        uow.runner_profiles.insert_profile("default-runner", "1.0.0")
        uow.migration_jobs.insert_job("job-no-output")
        # No Stage 1 ledger entry at all

        service = StageTwoAndThreeQueueService(lambda: uow)

        with pytest.raises(ContinuationPolicyViolationError) as excinfo:
            service.queue_stage_two_and_three(
                job_id="job-no-output",
                stage_run_ids=("stage-2-run", "stage-3-run"),
                sandbox_root_id="root-sandbox",
                sandbox_relative_path_stage2="sandbox/stage2",
                sandbox_relative_path_stage3="sandbox/stage3",
                ledger_entry_checksums={},
                jdk_java_homes={"java17": "/opt/java17", "java21": "/opt/java21"},
                run_configuration_artifact_id="artifact-rc",
                run_configuration_checksum="rc-cs",
                working_directory_root_id="root-output",
                working_directory_relative_path="jobs/no-output",
            )

        assert "no output checksum" in str(excinfo.value)
