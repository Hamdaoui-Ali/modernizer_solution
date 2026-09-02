from test_scanner import scan_tests


def test_scan_tests_inventory_legacy_modernized_missing(tmp_path):
    legacy_root = tmp_path / "legacy"
    modernized_root = tmp_path / "modernized"

    legacy_tests = legacy_root / "src" / "test" / "java" / "com" / "acme"
    modernized_tests = modernized_root / "src" / "test" / "java" / "com" / "acme"
    legacy_tests.mkdir(parents=True)
    modernized_tests.mkdir(parents=True)

    (legacy_tests / "AServiceTest.java").write_text("class AServiceTest {}")
    (legacy_tests / "BServiceTests.java").write_text("class BServiceTests {}")
    (modernized_tests / "AServiceTest.java").write_text("class AServiceTest {}")

    result = scan_tests(str(legacy_root), str(modernized_root))

    assert result["legacy_test_count"] == 2
    assert result["modernized_test_count"] == 1
    assert result["missing_tests_count"] == 1
    assert result["missing_tests"] == ["src/test/java/com/acme/BServiceTests.java"]
