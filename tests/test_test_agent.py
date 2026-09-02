from __future__ import annotations

import json
from pathlib import Path

from migration_factory.agents.test_agent import NO_SUREFIRE_REPORTS_WARNING, run_test_agent


def test_test_agent_parses_surefire_pass(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    reports = sandbox / "target" / "surefire-reports"
    reports.mkdir(parents=True)
    (reports / "TEST-A.xml").write_text(
        '<testsuite tests="3" failures="0" errors="0" skipped="1"></testsuite>',
        encoding="utf-8",
    )

    result = run_test_agent(
        sandbox_path=sandbox,
        run_dir=tmp_path / "run",
        run_id="run-001",
        source_log_path=tmp_path / "phase2.log",
        command=["mvn", "clean", "test"],
        cwd=str(sandbox),
        build_status="BUILD_PASSED_IN_SANDBOX",
        build_exit_code=0,
    )

    assert result.test_status == "TEST_PASSED"
    assert result.totals == {"tests": 3, "passed": 2, "failures": 0, "errors": 0, "skipped": 1}
    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["execution_mode"] == "parse_existing_surefire"
    assert payload["execution_owner"] == "build-agent"
    assert payload["build_status"] == "BUILD_PASSED_IN_SANDBOX"
    assert payload["build_exit_code"] == 0
    assert payload["reason"] == "SUREFIRE_REPORTS_PASSED"
    assert payload["parse_duration_seconds"] >= 0


def test_test_agent_parses_surefire_failed(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    reports = sandbox / "module" / "target" / "surefire-reports"
    reports.mkdir(parents=True)
    (reports / "TEST-A.xml").write_text(
        '<testsuite tests="2" failures="1" errors="0" skipped="0"></testsuite>',
        encoding="utf-8",
    )

    result = run_test_agent(
        sandbox_path=sandbox,
        run_dir=tmp_path / "run",
        run_id="run-001",
        source_log_path=tmp_path / "phase2.log",
    )

    assert result.test_status == "TEST_FAILED"
    assert result.totals["failures"] == 1


def test_test_agent_missing_reports_without_runnable_tests_is_warning_when_reports_not_required(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    source_dir = sandbox / "src" / "test" / "java" / "com" / "total" / "corp" / "translation" / "config"
    source_dir.mkdir(parents=True)
    (source_dir / "WebConfigurerTestController.java").write_text("class WebConfigurerTestController {}", encoding="utf-8")

    result = run_test_agent(
        sandbox_path=sandbox,
        run_dir=tmp_path / "run",
        run_id="run-001",
        source_log_path=tmp_path / "phase2.log",
        build_status="BUILD_PASSED_IN_SANDBOX",
        build_exit_code=0,
        require_test_reports=False,
    )

    assert result.test_status == "PASS_WITH_WARNINGS"
    assert result.report_paths == []
    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["warnings"] == [NO_SUREFIRE_REPORTS_WARNING]
    assert payload["detected_test_sources"] == ["WebConfigurerTestController.java"]
    assert payload["runnable_test_candidates"] == []
    assert payload["non_runnable_test_sources"] == ["WebConfigurerTestController.java"]
    assert payload["reason"] == "BUILD_PASSED_NO_SUREFIRE_REPORTS_NO_RUNNABLE_TESTS"


def test_test_agent_missing_reports_with_runnable_tests_warns_when_reports_not_required(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    source_dir = sandbox / "src" / "test" / "java" / "example"
    source_dir.mkdir(parents=True)
    (source_dir / "ExampleTest.java").write_text("class ExampleTest {}", encoding="utf-8")

    result = run_test_agent(
        sandbox_path=sandbox,
        run_dir=tmp_path / "run",
        run_id="run-001",
        source_log_path=tmp_path / "phase2.log",
        build_status="BUILD_PASSED_IN_SANDBOX",
        build_exit_code=0,
        require_test_reports=False,
    )

    assert result.test_status == "PASS_WITH_WARNINGS"
    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["runnable_test_candidates"] == ["ExampleTest.java"]
    assert payload["reason"] == "BUILD_PASSED_NO_SUREFIRE_REPORTS_RUNNABLE_TESTS_DETECTED"


def test_test_agent_missing_reports_is_error_when_reports_required(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    result = run_test_agent(
        sandbox_path=sandbox,
        run_dir=tmp_path / "run",
        run_id="run-001",
        source_log_path=tmp_path / "phase2.log",
        build_status="BUILD_PASSED_IN_SANDBOX",
        build_exit_code=0,
        require_test_reports=True,
    )

    assert result.test_status == "TEST_ERROR"
    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["reason"] == "REQUIRE_TEST_REPORTS_TRUE_NO_SUREFIRE_REPORTS"


def test_test_agent_build_failed_is_error(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    result = run_test_agent(
        sandbox_path=sandbox,
        run_dir=tmp_path / "run",
        run_id="run-001",
        source_log_path=tmp_path / "phase2.log",
        build_status="BUILD_FAILED_IN_SANDBOX",
        build_exit_code=1,
    )

    assert result.test_status == "TEST_ERROR"
    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["reason"] == "BUILD_COMMAND_FAILED"
    assert payload["build_exit_code"] == 1


def test_test_agent_malformed_report_is_error(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    reports = sandbox / "target" / "surefire-reports"
    reports.mkdir(parents=True)
    (reports / "TEST-A.xml").write_text("<testsuite", encoding="utf-8")

    result = run_test_agent(
        sandbox_path=sandbox,
        run_dir=tmp_path / "run",
        run_id="run-001",
        source_log_path=tmp_path / "phase2.log",
    )

    assert result.test_status == "TEST_ERROR"


def test_test_agent_skipped_only_is_pass(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    reports = sandbox / "target" / "surefire-reports"
    reports.mkdir(parents=True)
    (reports / "TEST-A.xml").write_text(
        '<testsuite tests="2" failures="0" errors="0" skipped="2"></testsuite>',
        encoding="utf-8",
    )

    result = run_test_agent(
        sandbox_path=sandbox,
        run_dir=tmp_path / "run",
        run_id="run-001",
        source_log_path=tmp_path / "phase2.log",
    )

    assert result.test_status == "TEST_PASSED"
    assert result.totals["passed"] == 0


def test_test_agent_build_failed_with_surefire_reports_still_parses_reports(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    reports = sandbox / "target" / "surefire-reports"
    reports.mkdir(parents=True)
    (reports / "TEST-A.xml").write_text(
        '<testsuite tests="4" failures="1" errors="0" skipped="0"></testsuite>',
        encoding="utf-8",
    )

    result = run_test_agent(
        sandbox_path=sandbox,
        run_dir=tmp_path / "run",
        run_id="run-001",
        source_log_path=tmp_path / "phase2.log",
        build_status="BUILD_FAILED_IN_SANDBOX",
        build_exit_code=1,
    )

    assert result.test_status == "TEST_FAILED"
    assert result.totals == {"tests": 4, "passed": 3, "failures": 1, "errors": 0, "skipped": 0}
    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["report_paths"] == [str(reports / "TEST-A.xml")]
    assert payload["reason"] == "SUREFIRE_REPORTS_CONTAIN_FAILURES"
