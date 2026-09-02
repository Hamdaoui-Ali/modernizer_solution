import json
import subprocess
from pathlib import Path

from openrewrite_adapter import run_openrewrite_dryrun


class DummyContext:
    def __init__(self, legacy_app_path: Path, output_dir: Path, modernized: Path):
        self.run_id = "test-run"
        self.legacy_app_path = str(legacy_app_path)
        self.output_dir = output_dir
        self.modernized_app_path = str(modernized)

    def get_output_path(self, name: str):
        return str(self.output_dir / name)


def _write_catalog(modernized: Path, goal="dryRun"):
    (modernized / ".migration").mkdir(parents=True, exist_ok=True)
    payload = {
        "openrewrite.plugin": "org.openrewrite.maven:rewrite-maven-plugin:5.40.0",
        "openrewrite.recipe_artifacts": "org.openrewrite.recipe:rewrite-spring:6.0.0",
        "openrewrite.active_recipes": "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
        "openrewrite.dry_run": goal,
    }
    (modernized / ".migration" / "ai_hub_profile.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_catalog_with_preview_args(modernized: Path):
    (modernized / ".migration").mkdir(parents=True, exist_ok=True)
    payload = {
        "openrewrite.plugin": "org.openrewrite.maven:rewrite-maven-plugin:5.40.0",
        "openrewrite.recipe_artifacts": "org.openrewrite.recipe:rewrite-spring:6.0.0",
        "openrewrite.active_recipes": "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
        "openrewrite.dry_run": "dryRun",
        "openrewrite.analysis_preview_maven_args": ["-Denforcer.skip=true"],
    }
    (modernized / ".migration" / "ai_hub_profile.json").write_text(json.dumps(payload), encoding="utf-8")


def test_missing_catalog_skips_cleanly(tmp_path):
    legacy = tmp_path / "legacy"
    output = tmp_path / "out"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    output.mkdir()
    modernized.mkdir()

    result = run_openrewrite_dryrun(DummyContext(legacy, output, modernized))
    assert result["status"] == "SKIPPED"
    assert (output / "rewrite_plugin_plan.json").exists()
    assert (output / "rewrite_preview.json").exists()


def test_success_captures_patch_and_no_pom_write(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    output = tmp_path / "out"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    output.mkdir()
    modernized.mkdir()
    _write_catalog(modernized)

    pom = legacy / "pom.xml"
    pom.write_text("<project/>", encoding="utf-8")
    before = pom.read_text(encoding="utf-8")

    patch = legacy / "rewrite.patch"
    patch.write_text("diff --git a/src/main/java/A.java b/src/main/java/A.java\n+import jakarta.x.Y;\n", encoding="utf-8")

    def _ok(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", _ok)

    result = run_openrewrite_dryrun(DummyContext(legacy, output, modernized))
    assert result["status"] == "USED"
    preview = json.loads((output / "rewrite_preview.json").read_text(encoding="utf-8"))
    assert preview["command"]
    assert preview["cwd"] == str(legacy)
    assert preview["exit_code"] == 0
    assert preview["patch_path"] == str(patch)
    assert preview["patch_produced"] is True
    assert "ok" in preview["stdout_tail"]
    plan = json.loads((output / "rewrite_plugin_plan.json").read_text(encoding="utf-8"))
    assert plan["schema_version"] == "1.0.0"
    assert plan["selected_preview_goal"] == "rewrite:dryRun"
    assert plan["apply_goals_forbidden"] is True
    assert (output / "rewrite_dry_run.patch").exists()
    assert (output / "rewrite_impact_summary.json").exists()
    impact = json.loads((output / "rewrite_impact_summary.json").read_text(encoding="utf-8"))
    assert impact["overall_impact"] == "LOW"
    assert impact["overall_impact"] != "BLOCKED"
    assert "impact" not in impact
    assert impact["schema_version"] == "1.0.0"
    assert impact["run_id"] == "test-run"
    assert impact["agent"] == "analysis_agent"
    assert impact["phase"] == "analysis"
    assert impact["changed_files"] == ["src/main/java/A.java"]
    assert isinstance(impact["migration_signals"], dict)
    assert pom.read_text(encoding="utf-8") == before


def test_rejects_forbidden_goal(tmp_path):
    legacy = tmp_path / "legacy"
    output = tmp_path / "out"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    output.mkdir()
    modernized.mkdir()
    _write_catalog(modernized, goal="run")

    result = run_openrewrite_dryrun(DummyContext(legacy, output, modernized))
    assert result["status"] == "FAILED"
    assert any("Forbidden OpenRewrite goal" in w for w in result["warnings"])


def test_source_modification_detection_fails(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    output = tmp_path / "out"
    modernized = tmp_path / "modernized"
    (legacy / "src" / "main" / "java").mkdir(parents=True)
    output.mkdir()
    modernized.mkdir()
    _write_catalog(modernized)

    source = legacy / "src" / "main" / "java" / "A.java"
    source.write_text("class A {}\n", encoding="utf-8")

    def _mutate(*args, **kwargs):
        source.write_text("class A { int x; }\n", encoding="utf-8")
        return subprocess.CompletedProcess(args[0], 0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", _mutate)

    result = run_openrewrite_dryrun(DummyContext(legacy, output, modernized))
    assert result["status"] == "FAILED"
    assert any("Source safety violation" in w for w in result["warnings"])


def test_adapter_uses_catalog_values_not_hardcoded(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    output = tmp_path / "out"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    output.mkdir()
    modernized.mkdir()
    _write_catalog(modernized)

    captured = {}

    def _capture(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", _capture)

    result = run_openrewrite_dryrun(DummyContext(legacy, output, modernized))
    assert result["status"] == "USED"
    assert "-Drewrite.activeRecipes=org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0" in captured["cmd"]
    assert "-Drewrite.recipeArtifactCoordinates=org.openrewrite.recipe:rewrite-spring:6.0.0" in captured["cmd"]


def test_dryrun_failure_records_stdout_stderr_diagnostic(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    output = tmp_path / "out"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    output.mkdir()
    modernized.mkdir()
    _write_catalog(modernized)

    (legacy / "pom.xml").write_text("<project/>", encoding="utf-8")

    def _fail(cmd, *args, **kwargs):
        raise subprocess.CalledProcessError(
            1,
            cmd,
            output="stdout detail\n" * 5,
            stderr="stderr detail\n" * 5,
        )

    monkeypatch.setattr("subprocess.run", _fail)

    result = run_openrewrite_dryrun(DummyContext(legacy, output, modernized))

    assert result["status"] == "FAILED"
    diagnostic = result["failure_diagnostic"]
    assert diagnostic["exit_code"] == 1
    assert diagnostic["command"]
    assert diagnostic["cwd"] == str(legacy)
    assert "stdout detail" in diagnostic["stdout_tail"]
    assert "stderr detail" in diagnostic["stderr_tail"]

    impact = json.loads((output / "rewrite_impact_summary.json").read_text(encoding="utf-8"))
    assert impact["status"] == "FAIL"
    assert impact["failure_diagnostic"]["exit_code"] == 1
    assert any("stdout detail" in reason for reason in impact["blocked_reasons"])
    assert any("stderr detail" in reason for reason in impact["blocked_reasons"])


def test_preview_only_enforcer_skip_warns_and_uses_analysis_command(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    output = tmp_path / "out"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    output.mkdir()
    modernized.mkdir()
    _write_catalog_with_preview_args(modernized)

    patch = legacy / "target" / "rewrite" / "rewrite.patch"
    patch.parent.mkdir(parents=True)
    patch.write_text("diff --git a/pom.xml b/pom.xml\n+<maven.compiler.release>21</maven.compiler.release>\n", encoding="utf-8")

    captured = {}

    def _ok(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", _ok)

    result = run_openrewrite_dryrun(DummyContext(legacy, output, modernized))

    assert result["status"] == "USED"
    assert "-Denforcer.skip=true" in captured["cmd"]
    assert any("preview only" in warning for warning in result["warnings"])
    preview = json.loads((output / "rewrite_preview.json").read_text(encoding="utf-8"))
    assert preview["patch_produced"] is True
    impact = json.loads((output / "rewrite_impact_summary.json").read_text(encoding="utf-8"))
    assert impact["status"] == "PASS"
    assert impact["overall_impact"] != "BLOCKED"
    assert any("final sandbox validation" in warning for warning in impact["warnings"])
