import json
from pathlib import Path

from context_manager import MigrationContext
from main import run_analysis_agent


def _seed_minimal_project(legacy: Path, modernized: Path):
    (legacy / "src/main/java").mkdir(parents=True)
    (legacy / "src/test/java").mkdir(parents=True)
    (legacy / "src/main/resources").mkdir(parents=True)

    (legacy / "pom.xml").write_text(
        """<project xmlns=\"http://maven.apache.org/POM/4.0.0\">\n"
        "  <modelVersion>4.0.0</modelVersion>\n"
        "  <groupId>com.example</groupId>\n"
        "  <artifactId>demo</artifactId>\n"
        "  <version>1.0.0</version>\n"
        "</project>\n""",
        encoding="utf-8",
    )
    (legacy / "src/main/java/App.java").write_text("import javax.servlet.Filter; class App {}\n", encoding="utf-8")
    (legacy / "src/test/java/AppTest.java").write_text("class AppTest {}\n", encoding="utf-8")
    (legacy / "src/main/resources/application.yml").write_text("server:\n  port: 8080\n", encoding="utf-8")

    (modernized / ".migration").mkdir(parents=True)
    profile = {
        "openrewrite.plugin": "org.openrewrite.maven:rewrite-maven-plugin:5.40.0",
        "openrewrite.recipe_artifacts": "org.openrewrite.recipe:rewrite-spring:6.0.0",
        "openrewrite.active_recipes": "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
        "openrewrite.dry_run": "dryRun",
    }
    (modernized / ".migration" / "ai_hub_profile.json").write_text(json.dumps(profile), encoding="utf-8")


def test_run_analysis_agent_returns_stable_result_shape(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    modernized.mkdir()
    _seed_minimal_project(legacy, modernized)

    monkeypatch.setattr("dependency_adapter.subprocess.run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("skip maven")))
    monkeypatch.setattr("openrewrite_adapter.subprocess.run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("skip rewrite")))

    ctx = MigrationContext("run-api", str(legacy), str(modernized))
    result = run_analysis_agent(ctx)

    assert result.status == "COMPLETED"
    assert result.rewrite_status in {"FAILED", "SKIPPED", "USED"}
    assert result.assist_status in {"USED", "SKIPPED", "FAILED", "SUCCESS"}
    assert set(result.artifact_paths).issuperset(
        {"analysis_report", "dependency_graph", "test_inventory", "analysis_summary"}
    )
    assert Path(result.artifact_paths["analysis_report"]).exists()
    assert Path(result.artifact_paths["analysis_summary"]).exists()
