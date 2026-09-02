from pathlib import Path

from readonly_verifier import build_read_only_verification, snapshot_tree


def _roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    output = modernized / ".migration" / "runs" / "run-1" / "analysis"
    (legacy / "src/main/java").mkdir(parents=True)
    (modernized / "src/main/java").mkdir(parents=True)
    output.mkdir(parents=True)
    (legacy / "pom.xml").write_text("<project />\n", encoding="utf-8")
    (legacy / "src/main/java/App.java").write_text("class App {}\n", encoding="utf-8")
    (modernized / "src/main/java/App.java").write_text("class AppModern {}\n", encoding="utf-8")
    return legacy, modernized, output


def _verify(legacy: Path, modernized: Path, output: Path, before_legacy: dict, before_modernized: dict) -> dict:
    return build_read_only_verification(
        run_id="run-1",
        legacy_root=legacy,
        modernized_root=modernized,
        before_legacy=before_legacy,
        before_modernized=before_modernized,
        output_dir=output,
    )


def test_readonly_verification_passes_for_analysis_artifact_only(tmp_path):
    legacy, modernized, output = _roots(tmp_path)
    before_legacy = snapshot_tree(legacy)
    before_modernized = snapshot_tree(modernized)

    (output / "analysis_report.json").write_text("{}\n", encoding="utf-8")

    verification = _verify(legacy, modernized, output, before_legacy, before_modernized)

    assert verification["status"] == "PASS"
    assert verification["source_modified"] is False
    assert verification["violations"] == []


def test_readonly_verification_fails_for_source_modification(tmp_path):
    legacy, modernized, output = _roots(tmp_path)
    before_legacy = snapshot_tree(legacy)
    before_modernized = snapshot_tree(modernized)

    (legacy / "src/main/java/App.java").write_text("class Changed {}\n", encoding="utf-8")
    (modernized / "Dockerfile").write_text("FROM eclipse-temurin:17\n", encoding="utf-8")

    verification = _verify(legacy, modernized, output, before_legacy, before_modernized)

    assert verification["status"] == "FAIL"
    assert verification["source_modified"] is True
    assert {"tree": "legacy", "path": "src/main/java/App.java", "change_type": "modified"} in verification[
        "violations"
    ]
    assert {"tree": "modernized", "path": "Dockerfile", "change_type": "added"} in verification["violations"]


def test_readonly_verification_ignores_build_and_cache_paths(tmp_path):
    legacy, modernized, output = _roots(tmp_path)
    before_legacy = snapshot_tree(legacy)
    before_modernized = snapshot_tree(modernized)

    (legacy / "target/classes").mkdir(parents=True)
    (legacy / "target/classes/App.class").write_bytes(b"class")
    (modernized / ".pytest_cache").mkdir()
    (modernized / ".pytest_cache/CACHEDIR.TAG").write_text("cache\n", encoding="utf-8")

    verification = _verify(legacy, modernized, output, before_legacy, before_modernized)

    assert verification["status"] == "PASS"
    assert verification["violations"] == []


def test_readonly_verification_ignores_openrewrite_patch_artifacts(tmp_path):
    legacy, modernized, output = _roots(tmp_path)
    before_legacy = snapshot_tree(legacy)
    before_modernized = snapshot_tree(modernized)

    (legacy / "rewrite.patch").write_text("diff --git a/pom.xml b/pom.xml\n", encoding="utf-8")
    (modernized / "rewrite.diff").write_text("diff --git a/src/main/java/App.java b/src/main/java/App.java\n", encoding="utf-8")

    verification = _verify(legacy, modernized, output, before_legacy, before_modernized)

    assert verification["status"] == "PASS"
    assert verification["source_modified"] is False
    assert verification["violations"] == []
