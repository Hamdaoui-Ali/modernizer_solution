from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from migration_factory.control_tower.application.v2_stage_progression import (
    TERMINAL_STAGE_INDEX,
)
from migration_factory.control_tower.domain.entities import ArtifactRecord
from migration_factory.final_report.writer import generate_final_migration_report
from migration_factory.final_report.pdf_writer import write_text_pdf_from_markdown

UnitOfWorkFactory = Callable[[], Any]

REPORT_ARTIFACT_KINDS = frozenset({
    "final_report_json",
    "final_report_markdown",
    "final_report_pdf",
})

REPORT_CONTENT_TYPES = {
    "final_report_json": "application/json",
    "final_report_markdown": "text/markdown",
    "final_report_pdf": "application/pdf",
}

_ARTIFACT_CREATED_BY = "v2-final-report-service"


@dataclass(frozen=True)
class V2ReportArtifactSummary:
    artifact_id: str
    kind: str
    checksum_sha256: str
    size_bytes: int
    content_type: str
    download_url: str


@dataclass(frozen=True)
class V2FinalReportEligibility:
    eligible: bool
    blockers: list[str]


@dataclass(frozen=True)
class V2FinalReportResult:
    job_id: str
    status: str
    eligible: bool
    blockers: list[str]
    generated_at: str | None
    input_checksum: str | None
    redacted_summary: str
    artifacts: tuple[V2ReportArtifactSummary, ...]


class V2FinalReportService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
    ) -> None:
        self._uow_factory = unit_of_work_factory

    def get_report_status(self, job_id: str) -> V2FinalReportResult:
        uow = self._uow_factory()
        if hasattr(uow, "transaction_mode"):
            uow.transaction_mode = "read"
        with uow:
            job = uow.v2_jobs.get(job_id) if hasattr(uow, "v2_jobs") else None
            if job is None:
                raise ValueError(f"V2 job {job_id!r} not found")

            eligibility = self._evaluate_eligibility(uow, job_id)
            artifacts = self._load_report_artifacts(uow, job_id)

            status = "not_generated"
            generated_at = None
            input_checksum = None
            redacted_summary = ""
            if artifacts:
                status = "generated"
                generated_at = artifacts[0].generated_at if hasattr(artifacts[0], "generated_at") else None
                input_checksum = artifacts[0].input_checksum if hasattr(artifacts[0], "input_checksum") else None
                redacted_summary = "Final report generated for this migration."

            return V2FinalReportResult(
                job_id=job_id,
                status=status,
                eligible=eligibility.eligible,
                blockers=eligibility.blockers,
                generated_at=generated_at,
                input_checksum=input_checksum,
                redacted_summary=redacted_summary,
                artifacts=tuple(
                    V2ReportArtifactSummary(
                        artifact_id=a.artifact_id,
                        kind=a.kind,
                        checksum_sha256=a.checksum_sha256,
                        size_bytes=a.size_bytes,
                        content_type=a.content_type,
                        download_url=a.download_url,
                    )
                    for a in artifacts
                ),
            )

    def generate_report(
        self,
        job_id: str,
    ) -> V2FinalReportResult:
        with self._uow_factory() as uow:
            job = uow.v2_jobs.get(job_id) if hasattr(uow, "v2_jobs") else None
            if job is None:
                raise ValueError(f"V2 job {job_id!r} not found")

            eligibility = self._evaluate_eligibility(uow, job_id)
            if not eligibility.eligible:
                return V2FinalReportResult(
                    job_id=job_id,
                    status="blocked",
                    eligible=False,
                    blockers=eligibility.blockers,
                    generated_at=None,
                    input_checksum=None,
                    redacted_summary="",
                    artifacts=(),
                )

            # Idempotency: return existing artifacts if already generated
            existing = self._load_report_artifacts(uow, job_id)
            if existing:
                return V2FinalReportResult(
                    job_id=job_id,
                    status="generated",
                    eligible=True,
                    blockers=[],
                    generated_at=existing[0].generated_at,
                    input_checksum=existing[0].input_checksum,
                    redacted_summary="Final report generated for this migration.",
                    artifacts=tuple(
                        V2ReportArtifactSummary(
                            artifact_id=a.artifact_id,
                            kind=a.kind,
                            checksum_sha256=a.checksum_sha256,
                            size_bytes=a.size_bytes,
                            content_type=a.content_type,
                            download_url=a.download_url,
                        )
                        for a in existing
                    ),
                )

            artifacts = self._generate_report_artifacts(uow, job_id)

            return V2FinalReportResult(
                job_id=job_id,
                status="generated",
                eligible=True,
                blockers=[],
                generated_at=artifacts[0].generated_at if artifacts else None,
                input_checksum=artifacts[0].input_checksum if artifacts else None,
                redacted_summary="Final report generated for this migration.",
                artifacts=tuple(
                    V2ReportArtifactSummary(
                        artifact_id=a.artifact_id,
                        kind=a.kind,
                        checksum_sha256=a.checksum_sha256,
                        size_bytes=a.size_bytes,
                        content_type=a.content_type,
                        download_url=a.download_url,
                    )
                    for a in artifacts
                ),
            )

    def _evaluate_eligibility(
        self,
        uow: Any,
        job_id: str,
    ) -> V2FinalReportEligibility:
        blockers: list[str] = []

        commands = (
            uow.v2_commands.list_by_job_and_stage(job_id, TERMINAL_STAGE_INDEX)
            if hasattr(uow, "v2_commands")
            else []
        )
        if not commands:
            blockers.append("Stage 4 has not been started yet.")
        else:
            terminal = commands[0]
            if terminal.status != "completed":
                blockers.append(f"Stage 4 is not completed (status: {terminal.status}).")
            elif terminal.result_json is None:
                blockers.append("Stage 4 has no result.")
            else:
                try:
                    result = json.loads(terminal.result_json)
                except (json.JSONDecodeError, TypeError):
                    result = {}
                if not _looks_like_success(result):
                    blockers.append("Stage 4 did not complete successfully.")

        if hasattr(uow, "phase_gates"):
            open_gates = uow.phase_gates.list_open(job_id) if hasattr(uow.phase_gates, "list_open") else []
            if open_gates:
                blockers.append("There are open phase gates that must be resolved.")

        if hasattr(uow, "artifact_revisions"):
            accepted = (
                uow.artifact_revisions.find_accepted(job_id, TERMINAL_STAGE_INDEX, "stage_output")
                if hasattr(uow.artifact_revisions, "find_accepted")
                else None
            )
            if accepted is None:
                blockers.append("No accepted Stage 4 output artifact revision exists.")

        eligible = len(blockers) == 0
        return V2FinalReportEligibility(eligible=eligible, blockers=blockers)

    def _load_report_artifacts(
        self,
        uow: Any,
        job_id: str,
    ) -> list[_ArtifactSnapshot]:
        snapshots: list[_ArtifactSnapshot] = []
        if hasattr(uow, "artifacts") and hasattr(uow.artifacts, "list_for_job"):
            records = uow.artifacts.list_for_job(job_id)
            for rec in records:
                if rec.artifact_type not in REPORT_ARTIFACT_KINDS:
                    continue
                snapshots.append(_ArtifactSnapshot(
                    artifact_id=rec.artifact_id,
                    kind=rec.artifact_type,
                    checksum_sha256=rec.checksum,
                    size_bytes=rec.size_bytes,
                    content_type=rec.content_type or "",
                    download_url=f"/v1/v2/jobs/{job_id}/report-artifacts/{rec.artifact_id}/download",
                    generated_at=rec.created_at,
                    input_checksum=rec.checksum,
                ))
        return snapshots

    def _generate_report_artifacts(
        self,
        uow: Any,
        job_id: str,
    ) -> list[_ArtifactSnapshot]:
        result: list[_ArtifactSnapshot] = []
        state: dict[str, Any] = {}
        report_dir = None

        if hasattr(uow, "v2_commands"):
            commands = uow.v2_commands.list_by_job_and_stage(job_id, TERMINAL_STAGE_INDEX)
            if commands and commands[0].result_json:
                try:
                    state = json.loads(commands[0].result_json)
                except (json.JSONDecodeError, TypeError):
                    state = {}
                if isinstance(state, dict):
                    sandbox_path = state.get("sandbox_path")
                    if sandbox_path:
                        report_dir = Path(str(sandbox_path)) / "final"
                        state["run_dir"] = str(Path(str(sandbox_path)))

        if report_dir is None:
            report_dir = Path(f"reports/{job_id}")
            state["run_dir"] = str(Path.cwd() / "reports" / job_id)

        report_dir.mkdir(parents=True, exist_ok=True)

        input_checksum = _compute_input_checksum(state)

        # Idempotency: check if artifacts with same input checksum already exist
        existing = self._load_report_artifacts(uow, job_id)
        if existing:
            return existing

        writer_result = generate_final_migration_report(state)
        if writer_result.blockers:
            raise ValueError(f"Report generation failed: {'; '.join(writer_result.blockers)}")

        json_path = Path(str(writer_result.artifact_refs.get("final_migration_report", report_dir / "migration_report.json")))
        md_path = Path(str(writer_result.artifact_refs.get("final_migration_summary", report_dir / "migration_summary.md")))
        pdf_path = report_dir / "full_migration_report.pdf"

        if md_path.is_file():
            write_text_pdf_from_markdown(str(md_path), str(pdf_path))

        from migration_factory.control_tower.domain.checksums import utc_now_text
        created_at = utc_now_text()

        for artifact_info in [
            (json_path, "final_report_json", "application/json"),
            (md_path, "final_report_markdown", "text/markdown"),
            (pdf_path, "final_report_pdf", "application/pdf"),
        ]:
            path, kind, content_type = artifact_info
            if not path.is_file():
                continue
            sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            size = path.stat().st_size
            artifact_id = f"report-{uuid4().hex}"
            relative_path = str(path.resolve())
            normalized = f"reports/{job_id}/{kind}"
            artifact_record = ArtifactRecord(
                artifact_id=artifact_id,
                job_id=job_id,
                stage_run_id=None,
                artifact_type=kind,
                registered_root_id="",
                relative_path=relative_path,
                normalized_relative_path=normalized,
                content_type=content_type,
                size_bytes=size,
                checksum_algorithm="sha256",
                checksum=sha256,
                created_at=created_at,
                created_by=_ARTIFACT_CREATED_BY,
            )
            uow.artifacts.insert(artifact_record)
            download_url = f"/v1/v2/jobs/{job_id}/report-artifacts/{artifact_id}/download"
            result.append(_ArtifactSnapshot(
                artifact_id=artifact_id,
                kind=kind,
                checksum_sha256=sha256,
                size_bytes=size,
                content_type=content_type,
                download_url=download_url,
                generated_at=created_at,
                input_checksum=input_checksum,
            ))

        return result


@dataclass
class _ArtifactSnapshot:
    artifact_id: str
    kind: str
    checksum_sha256: str
    size_bytes: int
    content_type: str
    download_url: str
    generated_at: str | None = None
    input_checksum: str | None = None


def _looks_like_success(result: dict[str, Any]) -> bool:
    final_status = str(result.get("final_status") or result.get("status") or "")
    return final_status in ("PASS", "TRANSFORM_APPLIED_IN_SANDBOX", "completed")


def _compute_input_checksum(state: dict[str, Any]) -> str:
    raw = json.dumps(state, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
