import json
from pathlib import Path

from rewrite_command_builder import build_rewrite_maven_command
from rewrite_catalog_loader import load_rewrite_catalog


class DummyContext:
    def __init__(self, legacy: Path, modernized: Path, ai_hub: Path | None = None, profile: str | None = None):
        self.legacy_app_path = str(legacy)
        self.modernized_app_path = str(modernized)
        self.ai_hub_path = str(ai_hub) if ai_hub else None
        self.profile = profile


def test_catalog_loads_valid_openrewrite_config(tmp_path):
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    (modernized / ".migration").mkdir(parents=True)

    payload = {
        "openrewrite.plugin": "org.openrewrite.maven:rewrite-maven-plugin:5.40.0",
        "openrewrite.recipe_artifacts": "org.openrewrite.recipe:rewrite-spring:6.0.0",
        "openrewrite.active_recipes": "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
        "openrewrite.dry_run": "dryRun",
    }
    (modernized / ".migration" / "ai_hub_profile.json").write_text(json.dumps(payload), encoding="utf-8")

    result = load_rewrite_catalog(DummyContext(legacy, modernized))
    assert result["status"] == "USED"
    assert result["openrewrite"]["plugin"].startswith("org.openrewrite.maven")


def test_missing_catalog_skipped_cleanly(tmp_path):
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    modernized.mkdir()

    result = load_rewrite_catalog(DummyContext(legacy, modernized))
    assert result["status"] == "SKIPPED"


def _write_ai_hub(hub: Path, profile_id="java17", catalog_path="catalogs/openrewrite/java17.yaml", preview_goals=None):
    preview_goals = preview_goals or ["dryRun", "dryRunNoFork"]
    (hub / "profiles").mkdir(parents=True)
    (hub / "catalogs" / "openrewrite").mkdir(parents=True)
    (hub / "profiles" / f"{profile_id}.yaml").write_text(
        f"""id: {profile_id}
openrewrite:
  catalog_path: {catalog_path}
""",
        encoding="utf-8",
    )
    (hub / catalog_path).write_text(
        """id: springboot-3.5-java17
plugin:
  group_id: org.openrewrite.maven
  artifact_id: rewrite-maven-plugin
  version: "6.39.0"
recipe_artifacts:
  - group_id: org.openrewrite.recipe
    artifact_id: rewrite-spring
    version: "6.30.4"
active_recipes:
  - org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5
preview_goals:
"""
        + "".join(f"  - {goal}\n" for goal in preview_goals)
        + """forbidden_apply_goals:
  - rewrite:run
  - rewrite:runNoFork
""",
        encoding="utf-8",
    )


def test_ai_hub_yaml_profile_resolves_catalog_and_selects_dryrun(tmp_path):
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    hub = tmp_path / "hub"
    legacy.mkdir()
    modernized.mkdir()
    _write_ai_hub(hub)

    result = load_rewrite_catalog(DummyContext(legacy, modernized, hub, "java17"))

    assert result["status"] == "USED"
    assert result["profile_id"] == "java17"
    assert result["catalog_id"] == "springboot-3.5-java17"
    assert result["path"] == str((hub / "catalogs/openrewrite/java17.yaml").resolve())
    assert result["openrewrite"]["dry_run"] == "rewrite:dryRun"
    assert result["openrewrite"]["plugin"] == "org.openrewrite.maven:rewrite-maven-plugin:6.39.0"
    assert result["openrewrite"]["recipe_artifacts"] == ["org.openrewrite.recipe:rewrite-spring:6.30.4"]
    assert result["openrewrite"]["active_recipes"] == [
        "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5"
    ]


def test_ai_hub_missing_path_and_profile_fail_clearly(tmp_path):
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    modernized.mkdir()

    missing_hub = load_rewrite_catalog(DummyContext(legacy, modernized, tmp_path / "missing", "java17"))
    assert missing_hub["status"] == "FAILED"
    assert "AI Hub path not found" in missing_hub["errors"][0]

    hub = tmp_path / "hub"
    hub.mkdir()
    missing_profile = load_rewrite_catalog(DummyContext(legacy, modernized, hub, "missing"))
    assert missing_profile["status"] == "FAILED"
    assert "AI Hub profile not found" in missing_profile["errors"][0]


def test_ai_hub_catalog_without_dryrun_is_blocked(tmp_path):
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    hub = tmp_path / "hub"
    legacy.mkdir()
    modernized.mkdir()
    _write_ai_hub(hub, preview_goals=["discover"])

    result = load_rewrite_catalog(DummyContext(legacy, modernized, hub, "java17"))

    assert result["status"] == "FAILED"
    assert "rewrite:dryRun" in result["errors"][0]


def test_real_boot4_java21_catalog_loads_multiple_artifacts_and_recipes(tmp_path):
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    modernized.mkdir()
    repo_root = Path(__file__).resolve().parents[5]
    hub = repo_root / "modernizer-solution-ai-hub"

    result = load_rewrite_catalog(
        DummyContext(legacy, modernized, hub, "springboot-2-java8-to-boot4-java21")
    )

    assert result["status"] == "USED"
    assert result["openrewrite"]["recipe_artifacts"] == [
        "org.openrewrite.recipe:rewrite-migrate-java:3.36.0",
        "org.openrewrite.recipe:rewrite-spring:6.31.0",
    ]
    assert result["openrewrite"]["active_recipes"] == [
        "org.openrewrite.java.migrate.UpgradeToJava21",
        "org.openrewrite.java.spring.boot4.UpgradeSpringBoot_4_0",
    ]
    assert result["openrewrite"]["analysis_preview_maven_args"] == ["-Denforcer.skip=true"]


def test_real_java17_profile_preview_command_unchanged(tmp_path):
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    modernized.mkdir()
    repo_root = Path(__file__).resolve().parents[5]
    hub = repo_root / "modernizer-solution-ai-hub"

    result = load_rewrite_catalog(
        DummyContext(legacy, modernized, hub, "springboot-2.7-to-3.5-java17")
    )

    assert result["status"] == "USED"
    assert result["openrewrite"].get("analysis_preview_maven_args") == []
    cmd = build_rewrite_maven_command(result["openrewrite"])
    assert "-Denforcer.skip=true" not in cmd
    assert cmd[1].endswith(":dryRun")


def test_real_minimal_library_catalog_loads_preview_only_targeted_recipes(tmp_path):
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    modernized.mkdir()
    repo_root = Path(__file__).resolve().parents[5]
    hub = repo_root / "modernizer-solution-ai-hub"

    result = load_rewrite_catalog(
        DummyContext(legacy, modernized, hub, "library-jakarta-java17-minimal")
    )

    assert result["status"] == "USED"
    assert result["openrewrite"]["plugin"] == "org.openrewrite.maven:rewrite-maven-plugin:6.40.0"
    assert result["openrewrite"]["recipe_artifacts"] == [
        "org.openrewrite.recipe:rewrite-migrate-java:3.35.0"
    ]
    assert result["openrewrite"]["active_recipes"] == [
        "org.openrewrite.java.migrate.UpgradeBuildToJava17",
        "org.openrewrite.java.migrate.jakarta.JavaxAnnotationMigrationToJakartaAnnotation",
        "org.openrewrite.java.migrate.jakarta.JavaxXmlBindMigrationToJakartaXmlBind",
        "org.openrewrite.java.migrate.jakarta.JavaxPersistenceToJakartaPersistence",
        "org.openrewrite.java.migrate.jakarta.JavaxServletToJakartaServlet",
    ]
    assert "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5" not in result["openrewrite"]["active_recipes"]
    assert "org.openrewrite.java.migrate.jakarta.JakartaEE10" not in result["openrewrite"]["active_recipes"]
    assert result["openrewrite"]["dry_run"] == "rewrite:dryRun"
    assert "rewrite:run" in result["forbidden_apply_goals"]
