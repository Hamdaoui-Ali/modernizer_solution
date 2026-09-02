import hashlib
import json
import subprocess
from pathlib import Path

from context_manager import MigrationContext
from main import run_analysis_agent


def _hash_tree(root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted([p for p in root.rglob("*") if p.is_file()])
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _seed_fixture(legacy: Path, modernized: Path):
    (legacy / "src/main/java/com/example").mkdir(parents=True)
    (legacy / "src/test/java/com/example").mkdir(parents=True)
    (legacy / "src/main/resources").mkdir(parents=True)

    (legacy / "pom.xml").write_text(
        """<project xmlns=\"http://maven.apache.org/POM/4.0.0\">\n"
        "  <modelVersion>4.0.0</modelVersion>\n"
        "  <groupId>com.example</groupId>\n"
        "  <artifactId>fixture</artifactId>\n"
        "  <version>1.0.0</version>\n"
        "  <dependencies>\n"
        "    <dependency><groupId>javax.servlet</groupId><artifactId>javax.servlet-api</artifactId><version>4.0.1</version></dependency>\n"
        "  </dependencies>\n"
        "</project>\n""",
        encoding="utf-8",
    )

    (legacy / "src/main/java/com/example/App.java").write_text(
        "package com.example;\nimport javax.servlet.Filter;\nclass App {}\n",
        encoding="utf-8",
    )
    (legacy / "src/test/java/com/example/AppTest.java").write_text("class AppTest {}\n", encoding="utf-8")
    (legacy / "src/main/resources/application.properties").write_text("server.port=8080\n", encoding="utf-8")

    (modernized / ".migration").mkdir(parents=True)
    profile = {
        "openrewrite.plugin": "org.openrewrite.maven:rewrite-maven-plugin:5.40.0",
        "openrewrite.recipe_artifacts": "org.openrewrite.recipe:rewrite-spring:6.0.0",
        "openrewrite.active_recipes": "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
        "openrewrite.dry_run": "dryRun",
    }
    (modernized / ".migration" / "ai_hub_profile.json").write_text(json.dumps(profile), encoding="utf-8")


def test_full_analysis_flow_generates_artifacts_without_source_writes(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    modernized.mkdir()
    _seed_fixture(legacy, modernized)

    before_hash = _hash_tree(legacy)

    def _fake_maven(cmd, *args, **kwargs):
        cmd_text = " ".join(cmd)
        if "dependency:tree" in cmd_text and "-DoutputType=json" in cmd_text:
            payload = {
                "artifact": "com.example:fixture:jar:1.0.0",
                "children": [
                    {"artifact": "javax.servlet:javax.servlet-api:jar:4.0.1", "children": []}
                ],
            }
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
        if "rewrite-maven-plugin" in cmd_text:
            return subprocess.CompletedProcess(cmd, 0, stdout="rewrite dry run ok", stderr="")
        raise RuntimeError(f"Unexpected command: {cmd}")

    monkeypatch.setattr("dependency_adapter.subprocess.run", _fake_maven)
    monkeypatch.setattr("openrewrite_adapter.subprocess.run", _fake_maven)

    ctx = MigrationContext("run-int", str(legacy), str(modernized))
    result = run_analysis_agent(ctx)

    assert result.status == "COMPLETED"

    required = ["analysis_report", "dependency_graph", "test_inventory", "analysis_summary"]
    for key in required:
        assert Path(result.artifact_paths[key]).exists(), f"missing {key}"

    optional = ["rewrite_preview", "rewrite_plugin_plan", "rewrite_impact_summary"]
    for key in optional:
        assert Path(result.artifact_paths[key]).exists(), f"missing optional {key}"

    after_hash = _hash_tree(legacy)
    assert after_hash == before_hash


def test_analysis_handles_missing_source_stack_contract(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    modernized.mkdir()
    _seed_fixture(legacy, modernized)

    def _fake_scan_root_pom(*args, **kwargs):
        return {
            "target_stack": {"java": "21", "spring_boot": "4.0.0"},
            "project_structure": {"modules": [], "module_count": 0},
            "warnings": ["source stack missing in scanner result"],
        }

    monkeypatch.setattr("main.scan_root_pom", _fake_scan_root_pom)
    monkeypatch.setattr("main.run_dependency_tree", lambda context: None)
    def _fake_rewrite(context, analysis_facts=None):
        Path(context.get_output_path("rewrite_impact_summary.json")).write_text(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "run_id": context.run_id,
                    "agent": "analysis_agent",
                    "phase": "analysis",
                    "status": "SKIPPED",
                    "overall_impact": "UNKNOWN",
                    "changed_files": [],
                    "high_risk_files": [],
                    "migration_signals": {},
                    "blocked_reasons": [],
                    "source_modified": False,
                    "artifact_refs": {"self": "rewrite_impact_summary.json"},
                }
            ),
            encoding="utf-8",
        )
        return {"status": "SKIPPED", "warnings": []}

    monkeypatch.setattr("main.run_openrewrite_dryrun", _fake_rewrite)

    ctx = MigrationContext("run-missing-source-stack", str(legacy), str(modernized))
    result = run_analysis_agent(ctx)

    assert result.status == "COMPLETED"
    for key in (
        "analysis_report",
        "analysis_summary",
        "read_only_verification",
        "rewrite_impact_summary",
    ):
        assert Path(result.artifact_paths[key]).exists(), f"missing {key}"

    report = json.loads(Path(result.artifact_paths["analysis_report"]).read_text(encoding="utf-8"))
    assert report["source_stack"] == {}
    verification = json.loads(Path(result.artifact_paths["read_only_verification"]).read_text(encoding="utf-8"))
    assert verification["source_modified"] is False
