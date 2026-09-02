from __future__ import annotations

import json
from unittest.mock import MagicMock, create_autospec

from migration_factory.control_tower.application.v2_final_report_service import (
    V2FinalReportService,
    V2FinalReportEligibility,
    V2FinalReportResult,
)


def _mock_uow(v2_jobs: MagicMock | None = None, v2_commands: MagicMock | None = None) -> MagicMock:
    uow = MagicMock()
    uow.v2_jobs = v2_jobs or MagicMock()
    uow.v2_commands = v2_commands or MagicMock()
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=None)
    return uow


def test_get_report_status_returns_not_generated_for_new_job() -> None:
    uow = _mock_uow()
    uow.v2_jobs.get.return_value = MagicMock(job_id="job123")
    uow.v2_commands.list_by_job_and_stage.return_value = []
    factory = MagicMock(return_value=uow)
    service = V2FinalReportService(factory)

    result = service.get_report_status("job123")

    assert result.job_id == "job123"
    assert result.status == "not_generated"
    assert result.eligible is False
    assert len(result.blockers) > 0


def test_generate_report_returns_blocked_when_ineligible() -> None:
    uow = _mock_uow()
    uow.v2_jobs.get.return_value = MagicMock(job_id="job123")
    uow.v2_commands.list_by_job_and_stage.return_value = []
    factory = MagicMock(return_value=uow)
    service = V2FinalReportService(factory)

    result = service.generate_report("job123")

    assert result.status == "blocked"
    assert result.eligible is False


def test_evaluate_eligibility_fails_without_stage4() -> None:
    uow = _mock_uow()
    uow.v2_commands.list_by_job_and_stage.return_value = []
    service = V2FinalReportService(MagicMock(return_value=uow))

    eligibility = service._evaluate_eligibility(uow, "job123")

    assert eligibility.eligible is False
    assert any("Stage 4" in b for b in eligibility.blockers)


def test_report_result_contains_no_path_fields() -> None:
    result = V2FinalReportResult(
        job_id="job123",
        status="not_generated",
        eligible=False,
        blockers=[],
        generated_at=None,
        input_checksum=None,
        redacted_summary="",
        artifacts=(),
    )
    d = {
        "job_id": result.job_id,
        "status": result.status,
        "eligible": result.eligible,
        "blockers": list(result.blockers),
        "generated_at": result.generated_at,
        "input_checksum": result.input_checksum,
        "redacted_summary": result.redacted_summary,
        "artifacts": [
            {
                "artifact_id": a.artifact_id,
                "kind": a.kind,
                "checksum_sha256": a.checksum_sha256,
                "size_bytes": a.size_bytes,
                "content_type": a.content_type,
                "download_url": a.download_url,
            }
            for a in result.artifacts
        ],
    }
    assert "run_dir" not in d
    assert "sandbox_path" not in d
    assert "run_report_json" not in d
    assert "run_report_markdown" not in d
    assert "run_report_pdf" not in d
