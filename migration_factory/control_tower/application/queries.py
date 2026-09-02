"""Read-only application queries for the Control Tower.

All queries return typed DTOs and never create events, audit records,
or mutate operational state.
"""

from __future__ import annotations

import json

from pathlib import Path

from migration_factory.control_tower.application.dto import (
    ArtifactDto,
    AuditRecordDto,
    CommandExecutionDto,
    CommandOutputWindowDto,
    MigrationJobDto,
    PipelineDefinitionDto,
    RunConfigurationDto,
    RunEventDto,
    RunnerProfileDto,
    StageChainEntryDto,
    StageRunDto,
)
from migration_factory.control_tower.application.services import UnitOfWorkFactory
from migration_factory.control_tower.domain.entities import V1ContextPackManifestRecord
from migration_factory.control_tower.domain.entities import V1ModelInvocationRecord
from migration_factory.control_tower.domain.errors import (
    EventCursorConflictError,
    InvalidEventCursorError,
    NotFoundError,
)


DEFAULT_PUBLIC_EVENT_REPLAY_BATCH_SIZE = 500


class ControlTowerQueryService:
    """Read-only queries for Control Tower operational state."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    # ── MigrationJob ────────────────────────────────────────────

    def get_migration_job(self, job_id: str) -> MigrationJobDto:
        with self._unit_of_work_factory() as uow:
            job = uow.migration_jobs.get(job_id)
            if job is None:
                raise NotFoundError("migration job", job_id)
            return job

    def get_active_migration_job(self) -> MigrationJobDto | None:
        with self._unit_of_work_factory() as uow:
            record = uow.migration_jobs.get_active_job()
            if record is None:
                return None
            return MigrationJobDto(
                job_id=record.job_id,
                version=record.version,
                status=record.status,
                active_slot=record.active_slot,
                last_event_sequence=record.last_event_sequence,
                created_at=record.created_at,
                updated_at=record.updated_at,
                started_at=record.started_at,
                finished_at=record.finished_at,
            )

    def list_migration_jobs(self) -> tuple[MigrationJobDto, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.migration_jobs.list()

    def list_command_executions(self, job_id: str) -> tuple[CommandExecutionDto, ...]:
        with self._unit_of_work_factory() as uow:
            if uow.migration_jobs.get(job_id) is None:
                raise NotFoundError("migration job", job_id)
            return uow.command_executions.list_for_job(job_id)

    # ── RunConfiguration ────────────────────────────────────────

    def get_run_configuration(self, job_id: str) -> RunConfigurationDto:
        with self._unit_of_work_factory() as uow:
            record = uow.run_configurations.get_for_job(job_id)
            if record is None:
                raise NotFoundError("run configuration", job_id)
            return RunConfigurationDto(
                run_configuration_id=record.run_configuration_id,
                job_id=record.job_id,
                schema_version=record.schema_version,
                runner_profile_id=record.runner_profile_id,
                runner_profile_version=record.runner_profile_version,
                pipeline_id=record.pipeline_id,
                pipeline_version=record.pipeline_version,
                target_proof_level=record.target_proof_level.value,
                enabled_gates=tuple(json.loads(record.enabled_gates_json)),
                policy=json.loads(record.policy_json),
                payload_json=record.payload_json,
                payload_checksum=record.payload_checksum,
                created_at=record.created_at,
            )

    # ── StageRun ─────────────────────────────────────────────────

    def list_stage_runs(self, job_id: str) -> tuple[StageRunDto, ...]:
        with self._unit_of_work_factory() as uow:
            records = uow.stage_runs.list_for_job(job_id)
            return tuple(
                StageRunDto(
                    stage_run_id=r.stage_run_id,
                    job_id=r.job_id,
                    stage_index=r.stage_index,
                    stage_id=r.stage_id,
                    status=r.status,
                    input_source=json.loads(r.input_source_json),
                    created_at=r.created_at,
                    started_at=r.started_at,
                    finished_at=r.finished_at,
                )
                for r in records
            )

    # ── Stage Chain (V1 Ledger) ──────────────────────────────────

    def get_stage_chain(self, job_id: str) -> tuple[StageChainEntryDto, ...]:
        """Return ordered, redacted stage chain entries from the V1 ledger.

        Raises NotFoundError if the job does not exist.
        Returns an empty tuple if the job exists but has no chain entries.
        """
        with self._unit_of_work_factory() as uow:
            job = uow.migration_jobs.get(job_id)
            if job is None:
                raise NotFoundError("migration job", job_id)
            records = uow.stage_chain_ledger.list_for_job(job_id)
            return tuple(
                StageChainEntryDto(
                    ledger_id=r.ledger_id,
                    job_id=r.job_id,
                    stage_index=r.stage_index,
                    stage_run_id=r.stage_run_id,
                    chain_status=r.chain_status,
                    input_source_kind=r.input_source_kind,
                    input_checksum=r.input_checksum,
                    output_artifact_id=r.output_artifact_id,
                    output_checksum=r.output_checksum,
                    output_registered_at=r.output_registered_at,
                    created_at=r.created_at,
                )
                for r in records
            )

    def get_continuation_policy_events(
        self, job_id: str
    ) -> tuple[StageChainEventRecord, ...]:
        """Return continuation policy events for a job."""
        from migration_factory.control_tower.domain.entities import StageChainEventRecord

        with self._unit_of_work_factory() as uow:
            return uow.stage_chain_ledger.list_events_for_job(job_id)

    # ── RunEvent ─────────────────────────────────────────────────

    def list_run_events(self, job_id: str) -> tuple[RunEventDto, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.run_events.list_for_job(job_id)

    def replay_run_events(
        self,
        job_id: str,
        *,
        after_sequence: int,
        limit: int = DEFAULT_PUBLIC_EVENT_REPLAY_BATCH_SIZE,
    ) -> tuple[RunEventDto, ...]:
        if after_sequence < 0:
            raise InvalidEventCursorError("after_sequence must be greater than or equal to 0")
        if limit < 1:
            raise InvalidEventCursorError("event replay limit must be greater than 0")
        with self._unit_of_work_factory() as uow:
            job = uow.migration_jobs.get(job_id)
            if job is None:
                raise NotFoundError("migration job", job_id)
            if after_sequence > job.last_event_sequence:
                raise InvalidEventCursorError(
                    "after_sequence cannot be greater than the latest committed event sequence"
                )
            return uow.run_events.list_for_job_after(job_id, after_sequence, limit)

    def latest_run_event_sequence(self, job_id: str) -> int:
        with self._unit_of_work_factory() as uow:
            job = uow.migration_jobs.get(job_id)
            if job is None:
                raise NotFoundError("migration job", job_id)
            return job.last_event_sequence

    # ── Artifact ─────────────────────────────────────────────────

    def list_artifacts(self, job_id: str) -> tuple[ArtifactDto, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.artifacts.list_for_job(job_id)

    # ── AuditRecord ──────────────────────────────────────────────

    def list_audit_records(self) -> tuple[AuditRecordDto, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.audit_records.list()

    def list_audit_records_for_job(self, job_id: str) -> tuple[AuditRecordDto, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.audit_records.list_for_job(job_id)

    # ── ModelInvocation ──────────────────────────────────────────

    def list_model_invocations(self) -> tuple[V1ModelInvocationRecord, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.v1_model_invocations.list()

    def list_model_invocations_for_job(
        self, job_id: str
    ) -> tuple[V1ModelInvocationRecord, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.v1_model_invocations.list_for_job(job_id)

    def get_model_invocation(
        self, invocation_id: str
    ) -> V1ModelInvocationRecord | None:
        with self._unit_of_work_factory() as uow:
            return uow.v1_model_invocations.get(invocation_id)

    # ── ContextPackManifest ──────────────────────────────────────

    def list_context_pack_manifests(self) -> tuple[V1ContextPackManifestRecord, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.v1_context_pack_manifests.list()

    def list_context_pack_manifests_for_job(
        self, job_id: str
    ) -> tuple[V1ContextPackManifestRecord, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.v1_context_pack_manifests.list_for_job(job_id)

    def get_context_pack_manifest(
        self, manifest_id: str
    ) -> V1ContextPackManifestRecord | None:
        with self._unit_of_work_factory() as uow:
            return uow.v1_context_pack_manifests.get(manifest_id)

    # ── RunnerProfile ────────────────────────────────────────────

    def get_runner_profile(
        self,
        runner_profile_id: str,
        runner_profile_version: str,
    ) -> RunnerProfileDto:
        with self._unit_of_work_factory() as uow:
            profile = uow.runner_profiles.get(runner_profile_id, runner_profile_version)
            if profile is None:
                raise NotFoundError(
                    "runner profile",
                    f"{runner_profile_id}/{runner_profile_version}",
                )
            return profile

    def list_runner_profiles(self) -> tuple[RunnerProfileDto, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.runner_profiles.list()

    # ── PipelineDefinition ───────────────────────────────────────

    def get_pipeline_definition(
        self,
        pipeline_id: str,
        pipeline_version: str,
    ) -> PipelineDefinitionDto:
        with self._unit_of_work_factory() as uow:
            pipeline = uow.pipeline_definitions.get(pipeline_id, pipeline_version)
            if pipeline is None:
                raise NotFoundError(
                    "pipeline definition",
                    f"{pipeline_id}/{pipeline_version}",
                )
            return pipeline

    def list_pipeline_definitions(self) -> tuple[PipelineDefinitionDto, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.pipeline_definitions.list()


    def get_command_output_window(
        self,
        job_id: str,
        command_id: str,
        *,
        stream: str,
        after_offset: int,
        max_bytes: int,
    ) -> CommandOutputWindowDto:
        """Read a bounded window from a command's stdout or stderr file.

        The read is safe for UTF-8 split boundaries, bounded by max_bytes,
        and returns the actual byte range read along with decoded text.
        """
        if stream not in ("stdout", "stderr"):
            raise InvalidEventCursorError(f"Invalid stream name: {stream!r}; must be 'stdout' or 'stderr'")
        if after_offset < 0:
            raise InvalidEventCursorError("after_offset must be greater than or equal to 0")
        if max_bytes < 1:
            raise InvalidEventCursorError("max_bytes must be greater than 0")

        log_path: Path | None = None
        cmd_status: CommandState | None = None

        with self._unit_of_work_factory() as uow:
            cmd = uow.command_executions.get(command_id)
            if cmd is None:
                raise NotFoundError("command execution", command_id)
            if cmd.job_id != job_id:
                raise NotFoundError("command execution for job", command_id)

            cmd_status = cmd.status
            log_path = _resolve_log_path(uow, cmd, stream)

        if log_path is None or not log_path.exists() or not log_path.is_file():
            return CommandOutputWindowDto(
                command_id=command_id,
                job_id=job_id,
                stream=stream,
                requested_offset=after_offset,
                start_offset=0,
                next_offset=0,
                data="",
                encoding="utf-8",
                replacement_characters_used=0,
                truncated=False,
                terminal=False,
                max_bytes=max_bytes,
            )

        file_size = log_path.stat().st_size
        terminal = _is_command_terminal(cmd_status)

        start_offset = min(after_offset, file_size)
        read_size = min(max_bytes, file_size - start_offset)

        with log_path.open("rb") as f:
            if start_offset > 0:
                f.seek(start_offset)
            raw = f.read(read_size)

        decoded, replacement_count = _decode_utf8_safe(raw)
        truncated = read_size >= max_bytes and (start_offset + read_size) < file_size

        next_offset = start_offset + len(raw)

        return CommandOutputWindowDto(
            command_id=command_id,
            job_id=job_id,
            stream=stream,
            requested_offset=after_offset,
            start_offset=start_offset,
            next_offset=next_offset,
            data=decoded,
            encoding="utf-8",
            replacement_characters_used=replacement_count,
            truncated=truncated,
            terminal=terminal,
            max_bytes=max_bytes,
        )

    def get_command_output_offsets(
        self,
        command_id: str,
    ) -> tuple[int, int]:
        with self._unit_of_work_factory() as uow:
            return uow.command_executions.get_output_offsets(command_id)


def _is_command_terminal(status) -> bool:
    from migration_factory.control_tower.domain.commands import (
        TERMINAL_COMMAND_STATES,
    )

    return status in TERMINAL_COMMAND_STATES


def _resolve_log_path(uow, cmd, stream: str) -> Path | None:
    """Resolve the full path to a command's stdout or stderr log file.

    Uses the runner profile's filesystem roots to resolve the working
    directory, then appends the log relative path from the manifest.
    """
    from migration_factory.control_tower.application.services import (
        _find_workspace_root,
    )
    from migration_factory.control_tower.domain.errors import WorkspacePathError

    if cmd.working_directory_root_id is None or cmd.working_directory_relative_path is None:
        return None

    # Get the runner profile to resolve the root path
    job_record = uow.migration_jobs.get(cmd.job_id)
    if job_record is None:
        return None

    # Need to get runner_profile_id and version from the run_configuration
    run_config_record = uow.run_configurations.get_for_job(cmd.job_id)
    if run_config_record is None:
        return None

    runner = uow.runner_profiles.get_exact(
        run_config_record.runner_profile_id,
        run_config_record.runner_profile_version,
    )
    if runner is None:
        return None

    try:
        root_path = _find_workspace_root(runner.payload, cmd.working_directory_root_id)
    except Exception:
        return None

    working_dir = root_path / cmd.working_directory_relative_path

    # Determine log relative path from manifest (default to logs/stdout.log or logs/stderr.log)
    manifest_dir = working_dir / "control" / "commands" / cmd.command_id
    manifest_path = manifest_dir / "command_manifest.json"

    if manifest_path.exists():
        from migration_factory.control_tower.domain.manifests import (
            CommandManifest,
        )

        manifest = CommandManifest.model_validate_json(manifest_path.read_bytes())
        rel_path = (
            manifest.stdout_relative_path if stream == "stdout"
            else manifest.stderr_relative_path
        )
    else:
        rel_path = f"logs/{stream}.log"

    return working_dir / rel_path


def _decode_utf8_safe(
    raw: bytes,
) -> tuple[str, int]:
    """Decode bytes as UTF-8, replacing invalid sequences with U+FFFD.

    Returns (decoded_text, replacement_count).
    """
    decoded = raw.decode("utf-8", errors="replace")
    replacement_count = decoded.count("\ufffd")
    return decoded, replacement_count


def parse_public_event_cursor(
    *,
    after_sequence: str | int | None,
    last_event_id: str | None,
    latest_sequence: int,
) -> int:
    query_sequence = _parse_optional_sequence(after_sequence, "after_sequence")
    header_sequence = _parse_optional_sequence(last_event_id, "Last-Event-ID")

    for sequence in (query_sequence, header_sequence):
        if sequence is not None and sequence > latest_sequence:
            raise InvalidEventCursorError(
                "event cursor cannot be greater than the latest committed event sequence"
            )

    if header_sequence is not None:
        if query_sequence is not None and query_sequence > header_sequence:
            raise EventCursorConflictError(header_sequence, query_sequence)
        return header_sequence

    sequence = query_sequence
    if sequence is None:
        sequence = 0
    return sequence


def _parse_optional_sequence(value: str | int | None, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        sequence = value
    else:
        text = value.strip()
        if not text:
            raise InvalidEventCursorError(f"{field_name} must be an integer")
        try:
            sequence = int(text, 10)
        except ValueError as exc:
            raise InvalidEventCursorError(f"{field_name} must be an integer") from exc
    if sequence < 0:
        raise InvalidEventCursorError(f"{field_name} must be greater than or equal to 0")
    return sequence
