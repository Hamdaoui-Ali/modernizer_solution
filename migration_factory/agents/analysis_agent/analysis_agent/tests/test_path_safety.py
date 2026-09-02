import os
from pathlib import Path

import pytest
import main as analysis_main
from context_manager import MigrationContext, SecurityViolationError
from dependency_adapter import run_dependency_tree


def test_write_guard_allows_only_run_analysis_subtree(tmp_path):
    legacy = tmp_path / "legacy-app"
    modernized = tmp_path / "modernized-app"
    legacy.mkdir()
    modernized.mkdir()

    ctx = MigrationContext("run-123", str(legacy), str(modernized))

    allowed = Path(ctx.get_output_path("nested/analysis_report.json"))
    assert str(allowed).startswith(str(modernized / ".migration" / "runs" / "run-123" / "analysis"))

    with pytest.raises(SecurityViolationError):
        ctx.get_output_path("../outside.json")


def test_context_read_paths_used_by_scanners(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy-app"
    modernized = tmp_path / "modernized-app"
    legacy.mkdir()
    modernized.mkdir()
    (legacy / "pom.xml").write_text("<project xmlns='http://maven.apache.org/POM/4.0.0'></project>", encoding="utf-8")

    captured = {}

    def _scan_root_pom(path):
        captured["pom"] = path
        return {"source_stack": {}, "project_structure": {}, "target_stack": {}}

    def _scan_java_imports(path):
        captured["imports"] = path
        return {}

    def _scan_config(path):
        captured["config"] = path
        return {}

    def _scan_tests(path):
        captured["tests"] = path
        return {}

    def _surefire(path):
        captured["surefire"] = path
        return {}

    monkeypatch.setattr(analysis_main, "scan_root_pom", _scan_root_pom)
    monkeypatch.setattr(analysis_main, "scan_java_imports", _scan_java_imports)
    monkeypatch.setattr(analysis_main, "scan_config_files", _scan_config)
    monkeypatch.setattr(analysis_main, "save_config_inventory", lambda *a, **k: None)
    monkeypatch.setattr(analysis_main, "scan_tests", _scan_tests)
    monkeypatch.setattr(analysis_main, "parse_surefire_reports", _surefire)
    monkeypatch.setattr(analysis_main, "save_test_inventory", lambda *a, **k: None)
    monkeypatch.setattr(analysis_main, "run_dependency_tree", lambda *a, **k: None)
    monkeypatch.setattr(analysis_main, "run_openrewrite_dryrun", lambda *a, **k: None)
    monkeypatch.setattr(analysis_main, "assemble_report", lambda *a, **k: {})
    monkeypatch.setattr(analysis_main, "enrich_with_ai", lambda *a, **k: {})
    monkeypatch.setattr(analysis_main, "generate_summary", lambda ctx, *a, **k: ctx.get_output_path("analysis_summary.md"))

    monkeypatch.setattr(
        "sys.argv",
        [
            "analysis_agent",
            "--run-id",
            "run-ctx",
            "--legacy",
            str(legacy),
            "--modernized",
            str(modernized),
        ],
    )

    analysis_main.main()

    expected_legacy = os.path.abspath(str(legacy))
    assert captured["pom"] == os.path.abspath(str(legacy / "pom.xml"))
    assert captured["imports"] == expected_legacy
    assert captured["config"] == expected_legacy
    assert captured["tests"] == expected_legacy
    assert captured["surefire"] == expected_legacy


def test_cli_requires_ai_hub_and_profile_together(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy-app"
    modernized = tmp_path / "modernized-app"
    hub = tmp_path / "hub"
    legacy.mkdir()
    modernized.mkdir()
    hub.mkdir()

    monkeypatch.setattr(
        "sys.argv",
        [
            "analysis_agent",
            "--run-id",
            "run-ctx",
            "--legacy",
            str(legacy),
            "--modernized",
            str(modernized),
            "--ai-hub",
            str(hub),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        analysis_main.main()

    assert exc.value.code == 2


def test_cli_passes_ai_hub_and_profile_to_context(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy-app"
    modernized = tmp_path / "modernized-app"
    hub = tmp_path / "hub"
    legacy.mkdir()
    modernized.mkdir()
    hub.mkdir()
    captured = {}

    def _run(ctx):
        captured["ai_hub"] = ctx.ai_hub_path
        captured["profile"] = ctx.profile
        return analysis_main.AnalysisResult(
            status="COMPLETED",
            artifact_paths={"analysis_report": "report.json", "analysis_summary": "summary.md"},
            warnings=[],
            errors=[],
            assist_status="SKIPPED",
            rewrite_status="SKIPPED",
        )

    monkeypatch.setattr(analysis_main, "run_analysis_agent", _run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "analysis_agent",
            "--run-id",
            "run-ctx",
            "--legacy",
            str(legacy),
            "--modernized",
            str(modernized),
            "--ai-hub",
            str(hub),
            "--profile",
            "java17",
        ],
    )

    analysis_main.main()

    assert captured["ai_hub"] == os.path.abspath(str(hub))
    assert captured["profile"] == "java17"


def test_analysis_artifact_write_does_not_modify_source_files(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy-app"
    modernized = tmp_path / "modernized-app"
    (legacy / "src/main/java").mkdir(parents=True)
    (modernized / "src/main/java").mkdir(parents=True)

    legacy_source = legacy / "src/main/java/App.java"
    modernized_source = modernized / "src/main/java/App.java"
    legacy_source.write_text("class App {}\n", encoding="utf-8")
    modernized_source.write_text("class AppModern {}\n", encoding="utf-8")

    ctx = MigrationContext("run-safe", str(legacy), str(modernized))

    before_legacy = legacy_source.read_text(encoding="utf-8")
    before_modernized = modernized_source.read_text(encoding="utf-8")

    def _fail_maven(*args, **kwargs):
        raise RuntimeError("mvn missing")

    monkeypatch.setattr("dependency_adapter.subprocess.run", _fail_maven)

    run_dependency_tree(ctx)

    assert legacy_source.read_text(encoding="utf-8") == before_legacy
    assert modernized_source.read_text(encoding="utf-8") == before_modernized
    assert Path(ctx.get_output_path("dependency_graph.json")).exists()
