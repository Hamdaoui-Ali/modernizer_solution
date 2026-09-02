from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from migration_factory.agents.test_agent import TestAgentResult
from migration_factory.contracts.build import BuildRunResult
from migration_factory.repair_loop.validation_runner import ValidationExecutionContext, run_validation_after_patch


def _build_result(*, result_kind: str, error_contract_path: Path) -> BuildRunResult:
    return BuildRunResult(
        succeeded=False,
        result_kind=result_kind,
        message="build failed",
        error_contract_path=error_contract_path,
        exit_code=1,
        matched_line="root exception",
        warnings=[],
        command=["mvn", "test"],
        cwd=error_contract_path.parent,
        command_duration_seconds=0.1,
    )


def _test_result(*, report_paths: list[str]) -> TestAgentResult:
    return TestAgentResult(
        test_status="TEST_FAILED",
        totals={"tests": 2, "passed": 1, "failures": 1, "errors": 0, "skipped": 0},
        report_path=Path("test_report.json"),
        summary_path=Path("test_summary.md"),
        log_path=Path("test_agent.log"),
        report_paths=report_paths,
        parse_duration_seconds=0.01,
    )


def test_validation_runner_does_not_block_when_surefire_reports_exist(tmp_path: Path) -> None:
    error_contract = tmp_path / "build-error.json"
    error_contract.write_text("{}", encoding="utf-8")
    context = ValidationExecutionContext(validation_command=("mvn", "test"))

    with patch("migration_factory.repair_loop.validation_runner.run_build_agent", return_value=_build_result(result_kind="dependency_error", error_contract_path=error_contract)), patch("migration_factory.repair_loop.validation_runner.run_test_agent", return_value=_test_result(report_paths=[str(tmp_path / "target" / "surefire-reports" / "TEST-A.xml")])):
        validation = run_validation_after_patch(
            run_id="run-1",
            run_dir=tmp_path,
            sandbox_path=tmp_path,
            attempt=1,
            validation_context=context,
            execution_env={"PATH": "C:/bin"},
        )

    assert validation.passed is False
    assert "build validation failed after repair patch" in validation.errors
    assert "test execution was blocked because compilation failed" not in validation.errors
    assert "test validation failed after repair patch" in validation.errors


def test_validation_runner_blocks_only_on_compilation_evidence(tmp_path: Path) -> None:
    error_contract = tmp_path / "build-error.json"
    error_contract.write_text("{}", encoding="utf-8")
    context = ValidationExecutionContext(validation_command=("mvn", "test"))

    with patch("migration_factory.repair_loop.validation_runner.run_build_agent", return_value=_build_result(result_kind="compilation_error", error_contract_path=error_contract)), patch("migration_factory.repair_loop.validation_runner.run_test_agent", return_value=_test_result(report_paths=[])):
        validation = run_validation_after_patch(
            run_id="run-1",
            run_dir=tmp_path,
            sandbox_path=tmp_path,
            attempt=1,
            validation_context=context,
            execution_env={"PATH": "C:/bin"},
        )

    assert validation.passed is False
    assert "build validation failed after repair patch" in validation.errors
    assert "test execution was blocked because compilation failed" in validation.errors
