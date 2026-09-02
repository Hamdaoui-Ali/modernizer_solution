import json
import os
import shutil
from pathlib import Path

import pytest

from openrewrite_adapter import run_openrewrite_dryrun


class DummyContext:
    def __init__(self, legacy: Path, out: Path, modernized: Path):
        self.legacy_app_path = str(legacy)
        self.output_dir = out
        self.modernized_app_path = str(modernized)

    def get_output_path(self, name: str):
        return str(self.output_dir / name)


@pytest.mark.skipif(
    os.environ.get("AIMF_RUN_REAL_OPENREWRITE_TESTS") != "1",
    reason="Set AIMF_RUN_REAL_OPENREWRITE_TESTS=1 to run real OpenRewrite integration tests",
)
def test_real_openrewrite_dry_run_is_non_destructive(tmp_path):
    if shutil.which("mvn") is None:
        pytest.skip("Maven not available")

    legacy = tmp_path / "legacy"
    out = tmp_path / "out"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    out.mkdir()
    modernized.mkdir()

    (legacy / "src/main/java/com/example").mkdir(parents=True)
    pom = legacy / "pom.xml"
    pom.write_text(
        """<project xmlns=\"http://maven.apache.org/POM/4.0.0\">\n"
        "  <modelVersion>4.0.0</modelVersion>\n"
        "  <groupId>com.example</groupId>\n"
        "  <artifactId>real-rewrite</artifactId>\n"
        "  <version>1.0.0</version>\n"
        "</project>\n""",
        encoding="utf-8",
    )
    source = legacy / "src/main/java/com/example/App.java"
    source.write_text("package com.example; class App {}\n", encoding="utf-8")

    (modernized / ".migration").mkdir(parents=True)
    profile = {
        "openrewrite.plugin": "org.openrewrite.maven:rewrite-maven-plugin:5.40.0",
        "openrewrite.recipe_artifacts": "org.openrewrite.recipe:rewrite-spring:6.0.0",
        "openrewrite.active_recipes": "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
        "openrewrite.dry_run": "discover",
    }
    (modernized / ".migration" / "ai_hub_profile.json").write_text(json.dumps(profile), encoding="utf-8")

    before_pom = pom.read_text(encoding="utf-8")
    before_source = source.read_text(encoding="utf-8")

    result = run_openrewrite_dryrun(DummyContext(legacy, out, modernized))

    assert result["status"] in {"USED", "FAILED", "SKIPPED"}
    assert (out / "rewrite_preview.json").exists()
    assert (out / "rewrite_impact_summary.json").exists()

    assert pom.read_text(encoding="utf-8") == before_pom
    assert source.read_text(encoding="utf-8") == before_source
