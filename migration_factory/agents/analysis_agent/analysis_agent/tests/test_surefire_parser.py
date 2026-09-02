from surefire_parser import parse_surefire_reports


def test_parse_surefire_reports_sums_and_handles_non_xml(tmp_path):
    reports = tmp_path / "target" / "surefire-reports"
    reports.mkdir(parents=True)

    (reports / "TEST-first.xml").write_text(
        '<testsuite tests="5" failures="1" errors="1" skipped="1"></testsuite>'
    )
    (reports / "TEST-second.xml").write_text(
        '<testsuite tests="3" failures="0" errors="1" skipped="0"></testsuite>'
    )
    (reports / "README.txt").write_text("ignore")

    result = parse_surefire_reports(str(tmp_path))

    assert result["available"] is True
    assert result["passed"] == 4
    assert result["failed"] == 1
    assert result["errors"] == 2
    assert result["skipped"] == 1


def test_parse_surefire_reports_missing_directory(tmp_path):
    result = parse_surefire_reports(str(tmp_path))

    assert result == {
        "available": False,
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
    }
