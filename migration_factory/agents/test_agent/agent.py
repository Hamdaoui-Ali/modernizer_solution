from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any
import xml.etree.ElementTree as ET


TEST_STATUS_PASSED = "TEST_PASSED"
TEST_STATUS_FAILED = "TEST_FAILED"
TEST_STATUS_ERROR = "TEST_ERROR"
TEST_STATUS_PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
TEST_STATUS_TESTS_NOT_FOUND = "TESTS_NOT_FOUND"
BUILD_STATUS_PASSED = "BUILD_PASSED_IN_SANDBOX"
BUILD_STATUS_FAILED = "BUILD_FAILED_IN_SANDBOX"
NO_SUREFIRE_REPORTS_WARNING = (
    "NO_SUREFIRE_REPORTS_FOUND: Maven test phase passed but no Surefire XML reports were produced."
)


@dataclass(frozen=True)
class TestAgentResult:
    test_status: str
    totals: dict[str, int]
    report_path: Path
    summary_path: Path
    log_path: Path
    report_paths: list[str]
    parse_duration_seconds: float


def run_test_agent(
    *,
    sandbox_path: str | Path,
    run_dir: str | Path,
    run_id: str,
    source_log_path: str | Path,
    command: list[str] | None = None,
    cwd: str | None = None,
    build_status: str | None = None,
    build_exit_code: int | None = None,
    require_test_reports: bool = False,
    pre_snapshot: dict[str, Any] | None = None,
) -> TestAgentResult:
    resolved_sandbox = Path(sandbox_path).expanduser().resolve()
    resolved_run_dir = Path(run_dir).expanduser().resolve()
    out_dir = resolved_run_dir / "test" / "post_transform"
    out_dir.mkdir(parents=True, exist_ok=True)

    report_path = out_dir / "test_report.json"
    summary_path = out_dir / "test_summary.md"
    log_path = out_dir / "test_agent.log"

    log_lines: list[str] = []
    report_paths: list[str] = []
    totals = {"tests": 0, "passed": 0, "failures": 0, "errors": 0, "skipped": 0}
    test_status = TEST_STATUS_ERROR
    warnings: list[str] = []
    reason = ""
    started = time.monotonic()
    surefire_report_dir = resolved_sandbox / "target" / "surefire-reports"
    surefire_report_dir_exists = surefire_report_dir.is_dir()
    detected_test_sources: list[str] = []
    runnable_test_candidates: list[str] = []
    non_runnable_test_sources: list[str] = []

    if not resolved_sandbox.is_dir():
        log_lines.append(f"Invalid sandbox path: {resolved_sandbox}")
        reason = "INVALID_SANDBOX_PATH"
    else:
        test_sources = _detect_test_sources(resolved_sandbox)
        detected_test_sources = [_relative_or_name(path, resolved_sandbox) for path in test_sources]
        runnable_paths = [path for path in test_sources if _is_surefire_default_candidate(path)]
        runnable_test_candidates = [_relative_or_name(path, resolved_sandbox) for path in runnable_paths]
        non_runnable_test_sources = [
            _relative_or_name(path, resolved_sandbox) for path in test_sources if path not in set(runnable_paths)
        ]

        if pre_snapshot is not None:
            candidates = filter_current_reports(pre_snapshot, surefire_report_dir)
        else:
            candidates = sorted(resolved_sandbox.glob("**/target/surefire-reports/TEST-*.xml"))
        report_paths = [str(path) for path in candidates]
        discovered_report_dirs = sorted({path.parent for path in candidates})
        if discovered_report_dirs:
            surefire_report_dir = discovered_report_dirs[0]
            surefire_report_dir_exists = True
        failure_details: list[dict[str, str]] = []
        if candidates:
            parse_error: str | None = None
            for report in candidates:
                try:
                    root = ET.parse(report).getroot()
                except (ET.ParseError, OSError) as exc:
                    parse_error = f"Unable to parse report {report}: {exc}"
                    break
                tests = _int_attr(root, "tests")
                failures = _int_attr(root, "failures")
                errors = _int_attr(root, "errors")
                skipped = _int_attr(root, "skipped")
                if min(tests, failures, errors, skipped) < 0:
                    parse_error = f"Malformed numeric attribute in report: {report}"
                    break
                passed = tests - failures - errors - skipped
                if passed < 0:
                    parse_error = f"Malformed suite counts in report: {report}"
                    break
                totals["tests"] += tests
                totals["failures"] += failures
                totals["errors"] += errors
                totals["skipped"] += skipped
                totals["passed"] += passed
                for testcase in root.findall("testcase"):
                    classname = testcase.attrib.get("classname", "")
                    name = testcase.attrib.get("name", "")
                    failure_el = testcase.find("failure")
                    error_el = testcase.find("error")
                    if failure_el is not None:
                        msg = failure_el.attrib.get("message", "")
                        ftype = failure_el.attrib.get("type", "")
                        failure_details.append({
                            "classname": classname,
                            "name": name,
                            "message": msg,
                            "type": ftype,
                            "kind": "failure",
                        })
                    elif error_el is not None:
                        msg = error_el.attrib.get("message", "")
                        ftype = error_el.attrib.get("type", "")
                        failure_details.append({
                            "classname": classname,
                            "name": name,
                            "message": msg,
                            "type": ftype,
                            "kind": "error",
                        })

            if parse_error:
                log_lines.append(parse_error)
                reason = "SUREFIRE_REPORT_PARSE_ERROR"
            elif totals["failures"] > 0 or totals["errors"] > 0:
                test_status = TEST_STATUS_FAILED
                reason = "SUREFIRE_REPORTS_CONTAIN_FAILURES"
            else:
                test_status = TEST_STATUS_PASSED
                reason = "SUREFIRE_REPORTS_PASSED"
        elif build_status and build_status != BUILD_STATUS_PASSED:
            log_lines.append(f"Build command did not pass: {build_status}")
            reason = "BUILD_COMMAND_FAILED"
            test_status = TEST_STATUS_ERROR
        else:
            warnings.append(NO_SUREFIRE_REPORTS_WARNING)
            log_lines.append(NO_SUREFIRE_REPORTS_WARNING)
            if build_status == BUILD_STATUS_PASSED and not require_test_reports and not runnable_paths:
                test_status = TEST_STATUS_PASS_WITH_WARNINGS
                reason = "BUILD_PASSED_NO_SUREFIRE_REPORTS_NO_RUNNABLE_TESTS"
            elif build_status == BUILD_STATUS_PASSED and not require_test_reports and runnable_paths:
                test_status = TEST_STATUS_PASS_WITH_WARNINGS
                reason = "BUILD_PASSED_NO_SUREFIRE_REPORTS_RUNNABLE_TESTS_DETECTED"
            elif require_test_reports:
                test_status = TEST_STATUS_ERROR
                reason = "REQUIRE_TEST_REPORTS_TRUE_NO_SUREFIRE_REPORTS"
            else:
                test_status = TEST_STATUS_ERROR
                reason = "NO_SUREFIRE_REPORTS_FOUND"

    source_log = str(Path(source_log_path).expanduser().resolve())
    payload = {
        "schema_version": "1.0.0",
        "agent": "test-agent",
        "run_id": run_id,
        "phase": "post_transform",
        "test_status": test_status,
        "build_status": build_status,
        "build_exit_code": build_exit_code,
        "totals": totals,
        "command": command or [],
        "cwd": cwd,
        "sandbox_path": str(resolved_sandbox),
        "execution_owner": "build-agent",
        "execution_mode": "parse_existing_surefire",
        "report_paths": report_paths,
        "warnings": warnings,
        "surefire_report_dir": str(surefire_report_dir),
        "surefire_report_dir_exists": surefire_report_dir_exists,
        "detected_test_sources": detected_test_sources,
        "runnable_test_candidates": runnable_test_candidates,
        "non_runnable_test_sources": non_runnable_test_sources,
        "test_failure_details": failure_details[:200],
        "reason": reason,
        "test_log_path": str(log_path),
        "source_log_path": source_log,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "artifact_refs": {
            "self": str(report_path),
            "summary": str(summary_path),
            "log": str(log_path),
        },
        "parse_duration_seconds": round(time.monotonic() - started, 6),
    }

    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_path.write_text(_summary_markdown(payload), encoding="utf-8")
    log_path.write_text("\n".join(log_lines) + ("\n" if log_lines else ""), encoding="utf-8")

    return TestAgentResult(
        test_status=test_status,
        totals=totals,
        report_path=report_path,
        summary_path=summary_path,
        log_path=log_path,
        report_paths=report_paths,
        parse_duration_seconds=float(payload["parse_duration_seconds"]),
    )


def _int_attr(root: ET.Element, attr: str) -> int:
    value = root.attrib.get(attr, "0")
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _detect_test_sources(sandbox: Path) -> list[Path]:
    return sorted(
        path
        for path in sandbox.glob("**/src/test/**/*.java")
        if path.is_file() and "target" not in path.parts
    )


def _is_surefire_default_candidate(path: Path) -> bool:
    name = path.name
    return (
        (name.startswith("Test") and name.endswith(".java"))
        or name.endswith("Test.java")
        or name.endswith("Tests.java")
        or name.endswith("TestCase.java")
    )


def _relative_or_name(path: Path, root: Path) -> str:
    return path.name


def _summary_markdown(payload: dict[str, Any]) -> str:
    totals = payload["totals"]
    lines = [
        "# Test Summary (Post Transform)",
        "",
        f"- test_status: {payload['test_status']}",
        f"- build_status: {payload.get('build_status')}",
        f"- reason: {payload.get('reason', '')}",
        f"- tests: {totals['tests']}",
        f"- passed: {totals['passed']}",
        f"- failures: {totals['failures']}",
        f"- errors: {totals['errors']}",
        f"- skipped: {totals['skipped']}",
        f"- execution_owner: {payload['execution_owner']}",
        f"- execution_mode: {payload['execution_mode']}",
        f"- source_log_path: {payload['source_log_path']}",
    ]
    if payload["report_paths"]:
        lines.append("- report_paths:")
        lines.extend([f"  - {path}" for path in payload["report_paths"]])
    else:
        lines.append("- report_paths: []")
    if payload.get("warnings"):
        lines.append("- warnings:")
        lines.extend([f"  - {warning}" for warning in payload["warnings"]])
    lines.append("")
    return "\n".join(lines)


def capture_surefire_report_index(reports_dir: Path) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    if not reports_dir.is_dir():
        return snapshot
    for path in sorted(reports_dir.glob("TEST-*.xml")):
        try:
            stat = path.stat()
            snapshot[path.name] = {
                "size": stat.st_size,
                "st_mtime_ns": stat.st_mtime_ns,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        except OSError:
            continue
    return snapshot


def filter_current_reports(
    pre_snapshot: dict[str, dict[str, Any]],
    reports_dir: Path,
) -> list[Path]:
    current: list[Path] = []
    if not reports_dir.is_dir():
        return current
    for path in sorted(reports_dir.glob("TEST-*.xml")):
        key = path.name
        if key not in pre_snapshot:
            current.append(path)
            continue
        try:
            stat = path.stat()
            prev = pre_snapshot[key]
            if (
                stat.st_size != prev["size"]
                or stat.st_mtime_ns != prev["st_mtime_ns"]
                or hashlib.sha256(path.read_bytes()).hexdigest() != prev["sha256"]
            ):
                current.append(path)
        except OSError:
            continue
    return current


